from dataclasses import dataclass, field


@dataclass(frozen=True)
class YieldRange:
    item_id: int
    qty_min: float
    qty_max: float

    @property
    def expected_qty(self) -> float:
        return (self.qty_min + self.qty_max) / 2


@dataclass(frozen=True)
class BonusOutput:
    item_id: int
    qty_min: float
    qty_max: float
    chance: float | None  # None = unknown rate; excluded from core ranking

    @property
    def expected_qty(self) -> float:
        return (self.qty_min + self.qty_max) / 2


@dataclass(frozen=True)
class ConversionEdge:
    recipe_id: int
    name: str
    process_type: str
    mastery_required: int
    inputs: tuple[tuple[int, float], ...]
    base_outputs: tuple[YieldRange, ...]
    bonus_outputs: tuple[BonusOutput, ...] = field(default_factory=tuple)
