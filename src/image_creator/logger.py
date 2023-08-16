from __future__ import annotations

import enum
import logging
import pathlib
import traceback
from collections.abc import Sequence
from typing import Any

import cli_ui as ui

Status = enum.Enum("Status", ["OK", "NOK", "NEUTRAL"])
Colors: dict[Status, ui.Color] = {
    Status.NEUTRAL: ui.reset,
    Status.OK: ui.green,
    Status.NOK: ui.red,
}
ui.warn = ui.UnicodeSequence(ui.brown, "⚠️", "[!]")


class Logger:
    """Custom cli_ui-based logger providing ~unified UI for steps and tasks

    Operations are either Steps or tasks within a Step
    - Steps are collections of tasks
    - UI displays Steps and Tasks differently using symbols and indentation
    - Most tasks are visually represented based on ~state (not recorded)
      - running: started but not ended)
      - succeeded: ended successfuly ; with an optional confirmation text
      - failed: ended unsuccessfuly ; providing reason

    Most of this logger's job is to abstract this visual organization behind a
    hierachical API:
    - start_step()
    - start_task()
    - succeed_task()
    - end_step()
    """

    def __init__(
        self,
        level: int | None = logging.INFO,
        progress_to: pathlib.Path | None = None,
    ):
        self.verbose = level
        self.progress_to = progress_to

        if level:
            self.setLevel(level)

        self.currently = None

    @property
    def ui(self):
        return ui

    def setLevel(self, level: int):  # noqa: N802 (similar API to stdlib logger)
        """reset logger's verbose config based on level"""
        ui.setup(
            verbose=level <= logging.DEBUG,
            quiet=level >= logging.WARNING,
            color="auto",
            title="image-creator",
            timestamp=False,
        )

    def message(self, *tokens, end: str = "\n", timed: bool = False):
        """Flexible message printing

        - end: controls carriage-return
        - timed: control wehther to prefix with time"""
        self.clear()
        if timed:
            ui.CONFIG["timestamp"] = True
        ui.message(" " * self.indent_level, *tokens, end=end)
        if timed:
            ui.CONFIG["timestamp"] = False

    def debug(self, text: str):
        self.clear()
        ui.debug(ui.indent(text, num=self.indent_level))

    def info(self, text: str, end: str = "\n"):
        self.clear()
        ui.info(ui.indent(text, num=self.indent_level), end=end)

    def warning(self, text: str):
        self.clear()
        ui.message(ui.brown, ui.indent(text, num=self.indent_level))

    def error(self, text: str):
        self.clear()
        ui.message(ui.bold, ui.red, ui.indent(text, num=self.indent_level))

    def exception(self, exc: Exception):
        ui.message(ui.red, "".join(traceback.format_exception(exc)))

    def critical(self, text: str):
        self.clear()
        ui.error(text)

    def fatal(self, text: str):
        self.critical(text)

    def table(self, data: Any, headers: str | Sequence[str]):
        ui.info_table(data=data, headers=headers)

    @property
    def with_progress(self) -> bool:
        """wether configured to write progress to an external machine-readable file"""
        return self.progress_to is not None

    @property
    def indent_level(self):
        """standard indentation level based on current ~position"""
        return {"step": 3, "task": 6}.get(self.currently, 0)

    def mark_as(self, what: str):
        """set new ~position of the logger: step or task"""
        self.currently = what

    def clear(self):
        """clear in-task or in-step same-line hanging to prevent writing to previous"""
        if self.currently in ("task",):
            ui.info("")

    def start_step(self, step: str):
        """Start a new Step, Step has no status and will be on a single line"""
        self.clear()
        self.mark_as("step")
        ui.CONFIG["timestamp"] = True
        ui.info_1(step)
        ui.CONFIG["timestamp"] = False

    def end_step(self):
        self.clear()
        self.mark_as(None)

    def start_task(self, task: str):
        """Start new task. Task is expectd to end"""
        self.clear()
        self.mark_as("task")
        ui.CONFIG["timestamp"] = True
        ui.message("  ", ui.bold, ui.blue, "=>", ui.reset, task, end=" ")
        ui.CONFIG["timestamp"] = False

    def end_task(self, success: bool | None = None, message: str | None = None):
        """End current task with custom success symbol and message"""
        tokens = [] if success is None else [ui.check if success else ui.cross]
        if message:
            tokens += [ui.brown, message]
        ui.message(*tokens)
        self.mark_as(None)

    def succeed_task(self, message: str | None = None):
        """End current task as successful with optional message"""
        self.end_task(success=True, message=message)

    def fail_task(self, message: str | None = None):
        """End current task as unsuccessful with optional message"""
        self.end_task(success=False, message=message)

    def add_task(self, name: str, message: str | None = None):
        """Single-call task with no status information"""
        self.start_task(name)
        if message:
            ui.message(*[ui.brown, message])
        else:
            ui.message()
        self.mark_as(None)

    def complete_download(
        self,
        name: str,
        *,
        size: str | None = None,
        extra: str | None = None,
        failed: bool = False,
    ):
        """record completed download, inside a task, potentially following progress"""
        tokens = ["    ", ui.warn if failed else ui.check, name]
        if size:
            tokens += [ui.brown, str(size)]
        if extra:
            tokens += [ui.reset, extra]
        self.message(*tokens, timed=True)

    def add_dot(self, status: Status = Status.NEUTRAL):
        """pytest-like colored dots indicating hidden operations status

        Must be cleared-out manually with a newline (ui.message())"""
        ui.message(Colors.get(status, Colors[Status.NEUTRAL]), ".", end="")

    def terminate(self):
        self.clear()
