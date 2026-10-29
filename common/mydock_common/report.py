"""Rapport de diagnostic lisible (utilisé par `mydock-agent doctor` et `mydock-dock doctor`)."""

from __future__ import annotations

from dataclasses import dataclass

OK, WARN, FAIL, INFO = "✔", "⚠", "✘", "·"


@dataclass
class Line:
    mark: str
    text: str


class Report:
    def __init__(self) -> None:
        self.lines: list[Line] = []

    def add(self, mark: str, text: str) -> None:
        self.lines.append(Line(mark, text))

    def section(self, title: str) -> None:
        self.lines.append(Line("", f"\n{title}"))

    @property
    def failed(self) -> bool:
        return any(line.mark == FAIL for line in self.lines)

    def render(self) -> str:
        return "\n".join(
            f"{line.mark} {line.text}".lstrip() if line.mark else line.text for line in self.lines
        )
