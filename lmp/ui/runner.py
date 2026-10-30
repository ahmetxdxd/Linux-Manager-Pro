"""Task window that runs long commands with a live log and progress.

Commands run one after another through QProcess. Output is line-buffered (partial lines are
joined with the next chunk) and "@@LMP {json}" lines are interpreted as progress messages.
The window stays OPEN when the task ends so the user can read the result and details.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
                             QPushButton, QVBoxLayout, QWidget)

from lmp.core.privileged import PKEXEC_DISMISSED, PKEXEC_NOT_AUTHORIZED, PROGRESS_PREFIX
from lmp.i18n import tr
from lmp.ui import icons, theme
from lmp.ui.background import is_alive, run_async


@dataclass
class Step:
    title: str
    argv: list[str] | None = None
    func: Callable | None = None          # a Python function instead of argv (runs in the background)
    env: dict[str, str] = field(default_factory=dict)
    uses_pkexec: bool = False
    continue_on_error: bool = False

    @property
    def interruptible(self) -> bool:
        """Only plain user processes can be stopped midway. A root process started through
        pkexec cannot be signalled by the user, and a Python step cannot be interrupted
        safely (it may be in the middle of moving files)."""
        return self.argv is not None and not self.uses_pkexec


def summarize_error(log: str, code: int, uses_pkexec: bool) -> str:
    """Distils raw output into a short, meaningful explanation for the user."""
    if uses_pkexec and code == PKEXEC_DISMISSED:
        return tr("Authorization was cancelled; nothing was changed.")
    if uses_pkexec and code == PKEXEC_NOT_AUTHORIZED and "not authorized" in log.lower():
        return tr("You are not authorised to do this (polkit denied it).")
    deps = re.findall(r"removing (\S+) breaks dependency '([^']+)' required by (\S+)", log)
    if deps:
        parts = [f"{req} → {pkg}" for pkg, _, req in deps[:5]]
        return tr("Could not remove: other packages depend on it ({parts}). Remove the "
                  "dependent packages first.").format(parts=", ".join(parts))
    if "target not found" in log:
        names = re.findall(r"target not found: (\S+)", log)
        return tr("Packages not found in the repositories: {names}").format(
            names=", ".join(names[:10]))
    if "unable to lock database" in log:
        return tr("The pacman database is locked: another package operation may be running.")
    if "conflicting files" in log or "exists in filesystem" in log:
        return tr("File conflict: the files to be installed already exist on the system "
                  "(see details).")
    if "failed retrieving file" in log or "Could not resolve host" in log:
        return tr("Download failed: check your internet connection or mirrors.")
    if "One or more PGP signatures could not be verified" in log:
        return tr("A PGP signature could not be verified. Try 'Skip PGP verification' in the "
                  "builder options.")
    for line in reversed(log.splitlines()):
        line = line.strip()
        # "hata" covers the Turkish-localised helper output
        if line.lower().startswith(("error:", "hata", "==> error", "==> hata")):
            return line
    return tr("The task failed (exit code {code}). See the log for details.").format(code=code)


class TaskDialog(QDialog):
    finished_ok = pyqtSignal(bool)

    def __init__(self, title: str, steps: list[Step], parent: QWidget | None = None,
                 success_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 280)
        self.steps = steps
        self.success_text = success_text or tr("Task completed.")
        self.index = -1
        self.success = False
        self.results: list[bool] = []
        self._buffer = ""
        self._step_log: list[str] = []
        self._process: QProcess | None = None
        self._cancelled = False
        self._interrupted = False      # the running process was actually stopped

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(28, 28)
        head.addWidget(self.state_icon)
        col = QVBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setProperty("role", "h2")
        self.status_lbl = QLabel(tr("Preparing…"))
        self.status_lbl.setProperty("role", "muted")
        self.status_lbl.setWordWrap(True)
        col.addWidget(self.title_lbl)
        col.addWidget(self.status_lbl)
        head.addLayout(col, 1)
        lay.addLayout(head)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setFixedHeight(8)
        lay.addWidget(self.bar)

        self.toggle = QPushButton(tr("Show details"))
        self.toggle.setProperty("role", "ghost")
        self.toggle.clicked.connect(self._toggle_log)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setProperty("role", "log")
        self.log.setMaximumBlockCount(20000)
        self.log.setVisible(False)
        lay.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.clicked.connect(self._cancel)
        self.close_btn = QPushButton(tr("Close"))
        self.close_btn.setProperty("role", "primary")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.close_btn)
        lay.addLayout(buttons)
        self._set_state("busy")

    # ------------------------------------------------------------------ #
    def run(self) -> bool:
        """Opens the window, runs the steps, and returns the success state once it is closed."""
        QTimer.singleShot(0, self._next)
        self.exec()
        return self.success

    def _set_state(self, state: str) -> None:
        p = theme.current()
        name, color = {"busy": ("clock", p.accent), "ok": ("check", p.success),
                       "fail": ("warning", p.danger)}[state]
        self.state_icon.setPixmap(icons.icon(name, color, 28).pixmap(28, 28))

    def _toggle_log(self) -> None:
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.toggle.setText(tr("Hide details") if visible else tr("Show details"))
        if visible and self.height() < 460:
            self.resize(self.width(), 520)

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _next(self) -> None:
        self.index += 1
        if self.index >= len(self.steps):
            # critical errors end the task early; reaching this point means it completed
            self._finish(True)
            return
        if self._cancelled:
            self._finish(False, tr("Stopped: the remaining steps were cancelled."))
            return
        step = self.steps[self.index]
        prefix = f"[{self.index + 1}/{len(self.steps)}] " if len(self.steps) > 1 else ""
        self.status_lbl.setText(prefix + step.title)
        self.bar.setRange(0, 0)
        self._step_log = []
        self._append(f"\n━━ {step.title}")
        self.cancel_btn.setToolTip("" if step.interruptible else tr(
            "This step cannot be interrupted safely; cancelling stops the task after it."))
        if self._process is not None:
            self._process.deleteLater()
            self._process = None
        if step.func is not None:
            run_async(step.func, on_done=self._func_done, on_error=self._func_failed,
                      on_progress=self._func_progress)
            return
        self._buffer = ""
        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        for k, v in step.env.items():
            env.insert(k, v)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._proc_finished)
        proc.errorOccurred.connect(self._proc_error)
        self._process = proc
        shown = " ".join(step.argv or [])
        self._append("$ " + (shown if len(shown) < 240 else shown[:240] + " …"))
        proc.start(step.argv[0], step.argv[1:])

    # --- Python step ---
    def _func_progress(self, percent: int | None, message: str = "") -> None:
        if percent is not None:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(percent))
            if message:
                self.status_lbl.setText(str(message))
        elif message:
            self._append(str(message))

    def _func_done(self, result) -> None:
        if isinstance(result, str) and result:
            self._append(result)
        self.results.append(True)
        self._next()

    def _func_failed(self, message: str) -> None:
        self._append(tr("ERROR: {message}").format(message=message))
        self.results.append(False)
        step = self.steps[self.index]
        if step.continue_on_error:
            self._next()
        else:
            self._finish(False, message)

    # --- Process step ---
    def _read(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buffer += data.replace("\r\n", "\n")
        *lines, self._buffer = re.split(r"[\n\r]", self._buffer)
        for line in lines:
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        if line.startswith(PROGRESS_PREFIX):
            try:
                msg = json.loads(line[len(PROGRESS_PREFIX):])
            except ValueError:
                return
            kind = msg.get("type")
            if kind == "progress":
                self.bar.setRange(0, 100)
                self.bar.setValue(int(msg.get("percent", 0)))
                if msg.get("message"):
                    self.status_lbl.setText(msg["message"])
            elif kind == "status":
                self.status_lbl.setText(msg.get("message", ""))
                self.bar.setRange(0, 0)
                self._append("→ " + msg.get("message", ""))
            elif kind in ("log", "error", "done"):
                self._append(msg.get("message", ""))
                self._step_log.append(msg.get("message", ""))
            return
        if line.strip():
            self._append(line)
            self._step_log.append(line)

    def _proc_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._process:
            step = self.steps[self.index]
            self._append(tr("Could not start the program: {name}").format(
                name=step.argv[0] if step.argv else "?"))
            self.results.append(False)
            self._finish(False, tr("'{name}' was not found. Is the required package installed?").format(
                name=step.argv[0] if step.argv else "?"))

    def _proc_finished(self, code: int, _status) -> None:
        if self._buffer:
            self._handle_line(self._buffer)
            self._buffer = ""
        step = self.steps[self.index]
        ok = code == 0 and not self._interrupted
        self.results.append(ok)
        if self._interrupted:
            self._finish(False, tr("The task was cancelled."))
            return
        if not ok and not step.continue_on_error:
            self._finish(False, summarize_error("\n".join(self._step_log), code,
                                                step.uses_pkexec))
            return
        if not ok:
            self._append(tr("(step failed, continuing: code {code})").format(code=code))
        self._next()

    def _cancel(self) -> None:
        if self._cancelled or self.close_btn.isEnabled():
            return
        self._cancelled = True
        self.cancel_btn.setEnabled(False)
        step = self.steps[self.index] if 0 <= self.index < len(self.steps) else None
        proc = self._process
        running = proc is not None and proc.state() != QProcess.ProcessState.NotRunning
        if step is not None and step.interruptible and running:
            self._interrupted = True
            self._append(tr("Cancelling…"))
            proc.terminate()
            # no waitForFinished(): it would freeze the window; force it a bit later instead
            QTimer.singleShot(3000, lambda: is_alive(proc)
                              and proc.state() != QProcess.ProcessState.NotRunning
                              and proc.kill())
        elif step is not None and (running or step.func is not None):
            # the current step finishes on its own, the remaining ones are skipped
            self._append(tr("The current step cannot be interrupted safely; the task stops "
                            "after it."))
            self.status_lbl.setText(tr("Stopping after the current step…"))
        else:
            self._finish(False, tr("The task was cancelled."))

    def _finish(self, ok: bool, message: str = "") -> None:
        if self.close_btn.isEnabled():
            return
        self.success = ok
        self.bar.setRange(0, 100)
        self.bar.setValue(100 if ok else self.bar.value())
        self.bar.setProperty("level", "" if ok else "danger")
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)
        self._set_state("ok" if ok else "fail")
        if ok and self.results and not all(self.results):
            self.status_lbl.setText(tr("Finished, but some steps failed. See the details."))
        else:
            self.status_lbl.setText(self.success_text if ok else message)
        if not ok and not self.log.isVisible():
            self._toggle_log()
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self.close_btn.setFocus()
        self.finished_ok.emit(ok)

    def reject(self) -> None:
        # Esc / closing the window must not interrupt a running task by accident
        if self.close_btn.isEnabled():
            super().reject()
