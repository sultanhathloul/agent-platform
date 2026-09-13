"""The seven guardrail checkpoints (README §10.1 plus MODEL_TIER from §19.3; ADR-0009)."""

from enum import StrEnum


class Checkpoint(StrEnum):
    INPUT = "INPUT"  # 1  user message + attachment metadata      (graph node)
    TOOL_LIST = "TOOL_LIST"  # 2  capabilities/tools visible to the model (graph node)
    PRE_TOOL = "PRE_TOOL"  # 3  tool name + args                        (tools/broker.py)
    POST_TOOL = "POST_TOOL"  # 4  tool result                             (tools/broker.py)
    OUTPUT = "OUTPUT"  # 5  draft answer + citations                (graph node)
    DATA = "DATA"  # 6  text leaving toward the user or memory  (graph node)
    MODEL_TIER = "MODEL_TIER"  # 7  tier ceilings                           (models/routing.py)
