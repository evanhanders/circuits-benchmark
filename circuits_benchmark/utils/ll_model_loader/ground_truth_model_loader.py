from typing import Optional, Tuple

from iit.utils.correspondence import Correspondence

from circuits_benchmark.transformers.hooked_tracr_transformer import HookedTracrTransformer
from circuits_benchmark.utils.ll_model_loader.ll_model_loader import LLModelLoader


class GroundTruthModelLoader(LLModelLoader):
  def get_output_suffix(self) -> str:
    return self.__str__()

  def __repr__(self) -> str:
    return self.__str__()

  def __str__(self) -> str:
    return f"ground_truth"

  def load_ll_model_and_correspondence(
      self,
      load_from_wandb: bool,
      device: str,
      output_dir: Optional[str] = None,
      same_size: bool = False,
  ) -> Tuple[Correspondence, HookedTracrTransformer]:
    assert not load_from_wandb, "Ground truth models cannot loaded from wandb"
    assert not same_size, "Ground truth models are never same size"

    hl_model = self.case.get_hl_model(device=device)
    corr = self.case.get_correspondence()
    return corr, hl_model
