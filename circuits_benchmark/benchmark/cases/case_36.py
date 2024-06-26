from typing import Set

from circuits_benchmark.benchmark.tracr_benchmark_case import TracrBenchmarkCase
from tracr.rasp import rasp


class Case36(TracrBenchmarkCase):
  def get_program(self) -> rasp.SOp:
    return make_emoji_sentiment_classifier(rasp.tokens)

  def get_task_description(self) -> str:
    return "Classifies each token as 'positive', 'negative', or 'neutral' based on emojis."

  def get_vocab(self) -> Set:
    return {"😊", "😢", "📘"}


def make_emoji_sentiment_classifier(sop: rasp.SOp) -> rasp.SOp:
    """
    Classifies each token as 'positive', 'negative', or 'neutral' based on emojis.

    Example usage:
      emoji_sentiment = make_emoji_sentiment_classifier(rasp.tokens)
      emoji_sentiment(["😊", "😢", "📘"])
      >> ["positive", "negative", "neutral"]
    """
    # Define mapping for emoji sentiment classification
    emoji_sentiments = {"😊": "positive", "😢": "negative", "📘": "neutral"}
    classify_sentiment = rasp.Map(lambda x: emoji_sentiments.get(x, "neutral"), sop)
    return classify_sentiment
