"""A real Inspect task; the dataset is baked into the adapter image."""
import json
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import GenerateConfig
from inspect_ai.scorer import exact
from inspect_ai.solver import generate


@task
def local_smoke():
    fixtures = json.loads(Path(__file__).with_name("fixtures.json").read_text())
    return Task(
        dataset=MemoryDataset([
            Sample(id=str(i), input=row["prompt"], target=row["answer"])
            for i, row in enumerate(fixtures, 1)
        ]),
        solver=generate(),
        scorer=exact(),
        config=GenerateConfig(
            max_tokens=64, temperature=0, max_connections=1, timeout=30, max_retries=2,
            cache=False,
        ),
    )
