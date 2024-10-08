import os
from argparse import Namespace

import torch
import wandb
from transformer_lens import HookedTransformer

from acdc.TLACDCCorrespondence import TLACDCCorrespondence
from circuits_benchmark.benchmark.benchmark_case import BenchmarkCase
from circuits_benchmark.commands.common_args import add_common_args, add_evaluation_common_ags
from circuits_benchmark.utils.circuit.circuit_eval import build_from_acdc_correspondence
from circuits_benchmark.utils.circuit.circuit import Circuit
from circuits_benchmark.utils.circuit.circuit_node import CircuitNode
from circuits_benchmark.transformers.hooked_tracr_transformer import HookedTracrTransformer
from circuits_benchmark.utils.iit._acdc_utils import get_gt_circuit
from circuits_benchmark.utils.iit.iit_hl_model import IITHLModel
from iit.model_pairs.iit_behavior_model_pair import IITBehaviorModelPair
from iit.utils.nodes import LLNode
from iit.utils import index, IITDataset
from iit.utils.eval_ablations import get_mean_cache, get_circuit_score
from circuits_benchmark.utils.ll_model_loader.ll_model_loader_factory import LLModelLoader, get_ll_model_loader_from_args


def setup_args_parser(subparsers):
    parser = subparsers.add_parser("gt_node_realism")
    add_common_args(parser)
    add_evaluation_common_ags(parser)

    parser.add_argument(
        "-m",
        "--mean",
        action="store_true",
        help="Use mean cache. Defaults to zero ablation if not provided",
    )
    parser.add_argument(
        "--batch_size", type=int, default=512, help="Batch size for evaluation"
    )
    parser.add_argument("--lambda-reg", type=float, default=1.0, help="Regularization")
    parser.add_argument(
        "--relative", type=int, default=1, help="Use relative scores"
    )
    parser.add_argument(
        "-wandb",
        "--use-wandb",
        action="store_true",
        help="Use wandb for logging",
    )
    parser.add_argument(
        "--max-len", type=int, default=1000, help="Max length of unique data"
    )


def make_everything_for_task(case: BenchmarkCase, args: Namespace) -> tuple[HookedTransformer, Circuit, HookedTransformer, LLModelLoader]:
    output_dir = args.output_dir
    
    hl_model = case.get_hl_model()
    if isinstance(hl_model, HookedTracrTransformer):
        hl_model = IITHLModel(hl_model, eval_mode=True)

    ll_model_loader = get_ll_model_loader_from_args(case, args)
    hl_ll_corr, ll_model = ll_model_loader.load_ll_model_and_correspondence(args.device, output_dir=output_dir, same_size=True)
    full_corr = TLACDCCorrespondence.setup_from_model(
            ll_model, use_pos_embed=True
        )
    full_circuit = build_from_acdc_correspondence(corr=full_corr)
    gt_circuit = get_gt_circuit(hl_ll_corr, full_circuit, ll_model.cfg.n_heads, case)

    return hl_model, gt_circuit, ll_model, ll_model_loader

def make_nodes_to_ablate(
    tl_model: HookedTransformer, hypothesis_nodes: list, verbose=False
):
    show = lambda *args, **kwargs: print(*args, **kwargs) if verbose else None
    attn = [
        # LLNode(f"blocks.{layer}.attn.hook_result", index.Ix[:, :, head])
        CircuitNode(f"blocks.{layer}.attn.hook_result", head)
        for layer in range(tl_model.cfg.n_layers)
        for head in range(tl_model.cfg.n_heads)
    ]
    mlps = [
        # LLNode(f"blocks.{layer}.hook_mlp_out", index.Ix[[None]])
        CircuitNode(f"blocks.{layer}.hook_mlp_out", None)
        for layer in range(tl_model.cfg.n_layers)
    ]
    nodes_to_ablate = Circuit()
    for node in attn + mlps:
        nodes_to_ablate.add_node(node)
        
    for node in hypothesis_nodes:
        if node in nodes_to_ablate:
            show(f"Not ablating node: {node}")
            nodes_to_ablate.remove_node(node)
            assert node.name in tl_model.hook_dict.keys(), ValueError(
                f"{node.name} not in {tl_model.hook_dict.keys()}"
            )
        else:
            show(f"Node {node} not in list")
    
    ll_nodes_to_ablate = []
    for node in nodes_to_ablate:
        if 'attn' in node.name:
            ll_nodes_to_ablate.append(LLNode(node.name, index.Ix[:, :, node.index]))
        else: 
            ll_nodes_to_ablate.append(LLNode(node.name, index.Ix[[None]]))
    return ll_nodes_to_ablate

def run_nodewise_ablation(case: BenchmarkCase, args: Namespace):
    use_mean_cache = args.mean
    use_wandb = args.use_wandb

    hl_model, gt_circuit, model, ll_model_loader = make_everything_for_task(case, args)

    model_pair = IITBehaviorModelPair(
        hl_model=hl_model,
        ll_model=model,
        corr={},
        training_args={},
    )

    unique_dataset = case.get_clean_data(max_samples=args.max_len, unique_data=True)
    test_set = IITDataset(unique_dataset, unique_dataset, every_combination=True)
    mean_cache = None
    if use_mean_cache:
        mean_cache = get_mean_cache(
            model_pair, unique_dataset, batch_size=args.batch_size
        )

    nodes_in_hypothesis = list(gt_circuit.nodes)
    nodes_to_ablate = make_nodes_to_ablate(model, nodes_in_hypothesis)
    print("Ablating nodes: ", *nodes_to_ablate, sep="\n")
    print("GT Circuit nodes: ", list(gt_circuit.nodes), sep="\n")
    score = get_circuit_score(
        model_pair,
        test_set,
        nodes_to_ablate,
        mean_cache,
        use_mean_cache=use_mean_cache,
        batch_size=args.batch_size,
        relative_change=bool(args.relative),
    )

    print(f"Score: {score}")
    # Save score to a file in results/gt_scores
    mean_cache_str = "mean" if use_mean_cache else "zero"
    if not os.path.exists(f"results/gt_scores_{mean_cache_str}"):
        os.makedirs(f"results/gt_scores_{mean_cache_str}")
    with open(f"results/gt_scores_{mean_cache_str}/{case.get_name()}_{ll_model_loader.get_output_suffix()}.txt", "w") as f:
        f.write(str(score))

    if use_wandb:
        name = f"gt_{case.get_name()}_{ll_model_loader.get_output_suffix()}"
        wandb.init(
            project="node_realism_gt", name=name
        )
        wandb.log({"score": score})
