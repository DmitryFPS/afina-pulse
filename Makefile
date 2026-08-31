.PHONY: install probe models run serve backfill archive

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"

probe:
	bash scripts/probe_hw.sh

models:
	bash scripts/pull_models.sh

run:
	python -m afina_watch --config configs/watch.yaml

backfill:
	python -m afina_watch backfill --days 7 --config configs/watch.yaml

archive:
	python -m afina_watch archive --close --config configs/watch.yaml

serve:
	python -m afina_watch serve --config configs/watch.yaml
