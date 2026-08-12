from __future__ import annotations

from dataclasses import dataclass


class InvalidTerytError(ValueError):
    """Kod TERYT nie wskazuje obsługiwanego poziomu administracyjnego."""


@dataclass(frozen=True)
class TerytCode:
    code: str
    level: str

    @property
    def woj_code(self) -> str:
        return self.code[:2]

    @property
    def pow_code(self) -> str | None:
        return self.code[:4] if len(self.code) >= 4 else None

    @property
    def gmi_code(self) -> str | None:
        return self.code if len(self.code) == 7 else None


def parse_teryt(value: str) -> TerytCode:
    code = str(value).strip()
    levels = {2: "wojewodztwo", 4: "powiat", 7: "gmina"}
    if not code.isdigit() or len(code) not in levels:
        raise InvalidTerytError("TERYT musi zawierać 2, 4 albo 7 cyfr")
    return TerytCode(code, levels[len(code)])
