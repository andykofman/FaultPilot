"""Plugin assembly for the airspeed_failure lane."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core.analysis import AnalyzerChain
from ...core.attempt_runner import AttemptRunner, StagedStrategy
from ...core.case_generator import CaseGenerator
from ...core.manifest import Manifest
from .analyzers import AirspeedFailureAnalyzer, AirspeedFailureVerdictPolicy
from .case_generator import AirspeedFailureCaseGenerator
from .config import AirspeedFailureConfig
from .control import AirspeedFailureMissionControl
from .environment import AirspeedFailureEnvironment
from .manifest import AirspeedFailureManifest
from .monitor import AirspeedFailureMonitor
from .stimulus import AirspeedFailureStimulus
from . import defaults


@dataclass
class AirspeedFailurePlugin:
    config: AirspeedFailureConfig
    case_generator: CaseGenerator
    environment: AirspeedFailureEnvironment
    manifest: Manifest

    def attempt_runner(self) -> AttemptRunner:
        return AttemptRunner(
            environment=self.environment,
            strategy=StagedStrategy(
                stimulus=AirspeedFailureStimulus(self.config),
                control=AirspeedFailureMissionControl(self.config),
                monitor=AirspeedFailureMonitor(self.config),
                analyzers=AnalyzerChain([AirspeedFailureAnalyzer()]),
                verdict_policy=AirspeedFailureVerdictPolicy(),
            ),
            manifest=self.manifest,
            artifact_root=self.config.campaign_root,
        )

    def attempt_dir_factory(self):
        def _factory(
            manifest: Manifest,
            case,
            attempt_index: int | None = None,
        ) -> Path:
            idx = (
                int(attempt_index)
                if attempt_index is not None
                else manifest.next_attempt_index(case)
            )
            return defaults.attempt_dir(self.config.campaign_root, case.case_id, idx)

        return _factory


def build_plugin(config: AirspeedFailureConfig | None = None) -> AirspeedFailurePlugin:
    if config is None:
        config = AirspeedFailureConfig()
    return AirspeedFailurePlugin(
        config=config,
        case_generator=AirspeedFailureCaseGenerator(config),
        environment=AirspeedFailureEnvironment(config),
        manifest=AirspeedFailureManifest(config.campaign_root),
    )
