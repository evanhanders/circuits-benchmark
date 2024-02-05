from typing import Set

from benchmark import vocabs
from benchmark.benchmark_case import BenchmarkCase
from benchmark.common_programs import shift_by
from tracr.rasp import rasp


class Case39(BenchmarkCase):
  def get_program(self) -> rasp.SOp:
    return make_token_trend_analysis(rasp.tokens)

  def get_vocab(self) -> Set:
    return vocabs.get_int_digits_vocab(count=3)


def make_token_trend_analysis(sop: rasp.SOp) -> rasp.SOp:
    """
    Analyzes the trend (increasing, decreasing, constant) of numeric tokens.

    Example usage:
      trend_analysis = make_token_trend_analysis(rasp.tokens)
      trend_analysis([1, 2, 3, 3, 2, 1])
      >> ["increasing", "increasing", "constant", "decreasing", "decreasing"]
    """
    prev_token = shift_by(1, sop)
    next_token = shift_by(-1, sop)
    first_part = rasp.SequenceMap(lambda x, y: "increasing" if y > x else ("decreasing" if y < x else "constant"), prev_token, sop)
    second_part = rasp.SequenceMap(lambda x, y: "increasing" if y < x else ("decreasing" if y > x else "constant"), sop, next_token)
    trend_analysis = rasp.SequenceMap(lambda x, y: x if y == "constant" else y, first_part, second_part)

    return trend_analysis