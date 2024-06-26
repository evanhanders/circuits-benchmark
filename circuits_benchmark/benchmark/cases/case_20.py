from typing import Set

from circuits_benchmark.benchmark import vocabs
from circuits_benchmark.benchmark.tracr_benchmark_case import TracrBenchmarkCase
from tracr.rasp import rasp


class Case20(TracrBenchmarkCase):
  def get_program(self) -> rasp.SOp:
    return make_spam_message_detector(rasp.tokens)

  def get_task_description(self) -> str:
    return "Detect spam messages based on appearance of spam keywords."

  def get_vocab(self) -> Set:
    return vocabs.get_words_vocab().union({"spam", "offer", "click", "now"})


def make_spam_message_detector(sop: rasp.SOp) -> rasp.SOp:
    """
    Detects spam messages based on keyword frequency.

    Example usage:
      spam_detector = make_spam_message_detector(rasp.tokens)
      spam_detector(["free", "offer", "click", "now"])
      >> "spam"
    """
    spam_keywords = {"free", "offer", "click", "now"}
    keyword_count = rasp.Map(lambda x: sum(x == keyword for keyword in spam_keywords), sop)
    is_spam = rasp.Map(lambda x: "spam" if x > 0 else "not spam", keyword_count)
    return is_spam
