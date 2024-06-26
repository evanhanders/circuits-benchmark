import os
import pickle
import shutil
from functools import partial

import torch
import wandb

from acdc.TLACDCCorrespondence import TLACDCCorrespondence
from acdc.docstring.utils import AllDataThings
from circuits_benchmark.benchmark.benchmark_case import BenchmarkCase
from circuits_benchmark.commands.common_args import add_common_args, add_evaluation_common_ags
from circuits_benchmark.metrics.validation_metrics import l2_metric
from circuits_benchmark.transformers.hooked_tracr_transformer import (
    HookedTracrTransformer,
)
from circuits_benchmark.utils.circuit.circuit_eval import evaluate_hypothesis_circuit, calculate_fpr_and_tpr, \
    build_from_acdc_correspondence
from circuits_benchmark.utils.edge_sp import train_edge_sp, save_edges
from circuits_benchmark.utils.iit.correspondence import TracrCorrespondence
from circuits_benchmark.utils.ll_model_loader.ll_model_loader_factory import get_ll_model_loader_from_args
from circuits_benchmark.utils.node_sp import train_sp
from subnetwork_probing.masked_transformer import CircuitStartingPointType, EdgeLevelMaskedTransformer
from subnetwork_probing.train import NodeLevelMaskedTransformer, iterative_correspondence_from_mask, \
    proportion_of_binary_scores


def setup_args_parser(subparsers):
    parser = subparsers.add_parser("sp")
    add_common_args(parser)
    add_evaluation_common_ags(parser)

    parser.add_argument("--using-wandb", action="store_true")
    parser.add_argument("--wandb-project", type=str, default="subnetwork-probing")
    parser.add_argument("--wandb-entity", type=str, required=False)
    parser.add_argument("--wandb-group", type=str, required=False)
    parser.add_argument("--wandb-dir", type=str, default="/tmp/wandb")
    parser.add_argument("--wandb-mode", type=str, default="online")
    parser.add_argument(
        "--wandb-run-name",
        type=str,
        required=False,
        default=None,
        help="Value for wandb_run_name",
    )
    parser.add_argument("--lr", type=float, default=0.01)
    parser.add_argument("--epochs", type=int, default=300)
    parser.add_argument("--verbose", type=int, default=1)
    parser.add_argument("--lambda-reg", type=float, default=1)
    parser.add_argument("--zero-ablation", type=int, default=0)
    parser.add_argument("--data-size", type=int, default=1000)
    parser.add_argument("--metric", type=str, choices=["l2", "kl"], default="l2")
    parser.add_argument("--edgewise", action="store_true")
    parser.add_argument("--num-examples", type=int, default=50)
    parser.add_argument("--seq-len", type=int, default=300)
    parser.add_argument("--n-loss-average-runs", type=int, default=4)
    parser.add_argument(
        "--torch-num-threads",
        type=int,
        default=0,
        help="How many threads to use for torch (0=all)",
    )
    parser.add_argument("--reset-subject", type=int, default=0)
    # parser.add_argument("--torch-num-threads", type=int, default=0)
    parser.add_argument("--print-stats", type=int, default=1, required=False)
    parser.add_argument("--print-every", type=int, default=1, required=False)
    parser.add_argument("--atol", type=float, default=5e-2, required=False)
    parser.add_argument(
        "--same-size", action="store_true", help="Use same size for ll model"
    )


def eval_fn(
    corr: TLACDCCorrespondence,
    ll_model: HookedTracrTransformer,
    hl_ll_corr: TracrCorrespondence,
    case: BenchmarkCase,
):
    sp_circuit = build_from_acdc_correspondence(corr=corr)
    return evaluate_hypothesis_circuit(
        sp_circuit,
        ll_model,
        hl_ll_corr,
        case=case,
        verbose=False,
        print_summary=False,
    )


def run_sp(
    case: BenchmarkCase,
    args,
    calculate_fpr_tpr: bool = True,
):
    print(args)

    ll_model_loader = get_ll_model_loader_from_args(case, args)
    hl_ll_corr, tl_model = ll_model_loader.load_ll_model_and_correspondence(
        load_from_wandb=args.load_from_wandb,
        device=args.device,
        output_dir=args.output_dir,
        same_size=args.same_size,
    )
    output_suffix = ll_model_loader.get_output_suffix()

    # Check that dot program is in path
    if not shutil.which("dot"):
        raise ValueError("dot program not in path, cannot generate graphs for ACDC.")

    if args.torch_num_threads > 0:
        torch.set_num_threads(args.torch_num_threads)

    metric_name = args.metric
    zero_ablation = True if args.zero_ablation else False
    using_wandb = args.using_wandb
    edgewise = args.edgewise
    use_pos_embed = True

    data_size = args.data_size
    base = case.get_clean_data(max_samples=int(1.2 * data_size))
    source = case.get_corrupted_data(max_samples=int(1.2 * data_size))
    toks_int_values = base.get_inputs()
    toks_int_labels = base.get_targets()
    toks_int_values_other = source.get_inputs()
    # toks_int_labels_other = source.get_correct_outputs() # sp doesn't need this

    with torch.no_grad():
        baseline_output = tl_model(toks_int_values[:data_size])
        test_baseline_output = tl_model(toks_int_values[data_size:])

    if metric_name == "l2":
        validation_metric = partial(
            l2_metric,
            baseline_output=baseline_output,
            is_categorical=tl_model.is_categorical(),
        )
        test_loss_metric = partial(
            l2_metric,
            baseline_output=test_baseline_output,
            is_categorical=tl_model.is_categorical(),
        )
        test_accuracy_fn = (
            lambda x, y: torch.isclose(x, y, atol=args.atol).float().mean()
        )
        test_accuracy_metric = partial(test_accuracy_fn, test_baseline_output)
    elif metric_name == "kl":
        kl_metric = (
            lambda x, y: torch.nn.functional.kl_div(
                torch.nn.functional.log_softmax(x, dim=-1),
                torch.nn.functional.softmax(y, dim=-1),
                reduction="none",
            )
            .sum(dim=-1)
            .mean()
        )

        validation_metric = partial(kl_metric, y=baseline_output)
        test_loss_metric = partial(kl_metric, y=test_baseline_output)
        test_accuracy_fn = (
            lambda x, y: (x.argmax(dim=-1) == y.argmax(dim=-1)).float().mean()
        )
        test_accuracy_metric = partial(test_accuracy_fn, test_baseline_output)
    else:
        raise NotImplementedError(f"Metric {metric_name} not implemented")
    test_metrics = {"loss": test_loss_metric, "accuracy": test_accuracy_metric}

    all_task_things = AllDataThings(
        tl_model=tl_model,
        validation_metric=validation_metric,
        validation_data=toks_int_values[:data_size],
        validation_labels=toks_int_labels[:data_size],
        validation_mask=None,
        validation_patch_data=toks_int_values_other[:data_size],
        test_metrics=test_metrics,
        test_data=toks_int_values[data_size:],
        test_labels=toks_int_labels[data_size:],
        test_mask=None,
        test_patch_data=toks_int_values_other[data_size:],
    )

    output_dir = os.path.join(
        args.output_dir,
        f"{'edge_' if args.edgewise else 'node_'}sp_{case.get_name()}",
        output_suffix,
        f"lambda_{args.lambda_reg}",
    )
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    images_output_dir = os.path.join(output_dir, "images")
    if not os.path.exists(images_output_dir):
        os.makedirs(images_output_dir)

    # Setup wandb if needed
    if args.wandb_run_name is None:
        args.wandb_run_name = f"SP_{'edge' if edgewise else 'node'}_{case.get_name()}_reg_{args.lambda_reg}{'_zero' if zero_ablation else ''}"

    args.wandb_name = args.wandb_run_name

    tl_model.reset_hooks()
    if edgewise:
        masked_model = EdgeLevelMaskedTransformer(
            tl_model,
            starting_point_type=(
                CircuitStartingPointType.RESID_PRE
                if not use_pos_embed
                else CircuitStartingPointType.POS_EMBED
            ),
        )
    else:
        masked_model = NodeLevelMaskedTransformer(tl_model)
    masked_model = masked_model.to(args.device)

    masked_model.freeze_weights()
    print("Finding subnetwork...")
    if edgewise:
        eval_fn_to_use = partial(
            eval_fn, ll_model=tl_model, hl_ll_corr=hl_ll_corr, case=case
        )
        masked_model, log_dict = train_edge_sp(
            args=args,
            masked_model=masked_model,
            all_task_things=all_task_things,
            print_every=args.print_every,
            eval_fn=eval_fn_to_use,
        )
        percentage_binary = masked_model.proportion_of_binary_scores()
        sp_corr = masked_model.get_edge_level_correspondence_from_masks(
            use_pos_embed=use_pos_embed
        )
        sp_circuit = build_from_acdc_correspondence(corr=sp_corr)
    else:
        masked_model, log_dict = train_sp(
            args=args,
            masked_model=masked_model,
            all_task_things=all_task_things,
        )

        percentage_binary = proportion_of_binary_scores(masked_model)
        sp_corr, _ = iterative_correspondence_from_mask(
            masked_model.model,
            log_dict["nodes_to_mask"],
            use_pos_embed=use_pos_embed,
        )
        sp_circuit = build_from_acdc_correspondence(corr=sp_corr)

    # Update dict with some different things
    # log_dict["nodes_to_mask"] = list(map(str, log_dict["nodes_to_mask"]))
    # to_log_dict["number_of_edges"] = corr.count_no_edges() TODO
    log_dict["percentage_binary"] = percentage_binary
    # save sp circuit edges
    save_edges(sp_corr, f"{output_dir}/edges.pkl")

    if calculate_fpr_tpr:
        print("Calculating FPR and TPR for regularizer", args.lambda_reg)
        full_corr = TLACDCCorrespondence.setup_from_model(
            tl_model, use_pos_embed=True
        )
        full_circuit = build_from_acdc_correspondence(corr=full_corr)
        result = evaluate_hypothesis_circuit(
            sp_circuit,
            tl_model,
            hl_ll_corr,
            case=case,
            full_circuit=full_circuit,
            verbose=False,
        )
        # save results
        pickle.dump(result, open(f"{output_dir}/result.pkl", "wb"))
    else:
        result = {}

    if calculate_fpr_tpr:
        nodes_fpr = result["nodes"]["fpr"]
        nodes_tpr = result["nodes"]["tpr"]
        edges_fpr = result["edges"]["fpr"]
        edges_tpr = result["edges"]["tpr"]
        if using_wandb:
            wandb.log(
                {
                    "regularizer": args.lambda_reg,
                    "nodes_fpr": nodes_fpr,
                    "nodes_tpr": nodes_tpr,
                    "edges_fpr": edges_fpr,
                    "edges_tpr": edges_tpr,
                    "percentage_binary": percentage_binary,
                }
            )
        
    if args.using_wandb:
        wandb.init(
            project=f"circuit_discovery{'_same_size' if args.same_size else ''}",
            group=f"{'edge' if edgewise else 'node'}_sp_{case.get_name()}_{weights}",
            name=f"{args.lambda_reg}",
        )
        wandb.save(f"{output_dir}/*", base_path=args.output_dir)

    return sp_circuit, result
