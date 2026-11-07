.PHONY: db migrate db-down seed data train api web test lint train-image train-image-verify train-docker train-suite-docker train-local figures figures-local

PSQL = docker compose exec -T -e PGOPTIONS=--client-min-messages=warning db psql -U ppc -d ppc -v ON_ERROR_STOP=1 -q
# Runs `python -m <pkg>` if the module has added its entry point (CONTRACTS: make targets).
run_pkg = @if [ -f $(1)/__main__.py ]; then uv run $(2) python -m $(1); \
	else echo "$(1): entry point $(1)/__main__.py not implemented yet"; fi

db:
	docker compose up -d --wait db
	$(MAKE) migrate

migrate:
	@$(PSQL) -c "CREATE TABLE IF NOT EXISTS schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
	@for f in $$(ls db/migrations/*.sql 2>/dev/null | sort); do \
		name=$$(basename $$f); \
		done=$$($(PSQL) -tAc "SELECT 1 FROM schema_migrations WHERE filename = '$$name'"); \
		if [ -z "$$done" ]; then \
			echo "applying $$name"; \
			{ cat $$f; echo; echo "INSERT INTO schema_migrations (filename) VALUES ('$$name');"; } \
				| $(PSQL) -1 || exit 1; \
		fi; \
	done; echo "migrations up to date"

db-down:
	docker compose down

seed:
	$(call run_pkg,stations)

data:
	$(call run_pkg,dataset)

train:
	$(call run_pkg,model,--extra rl)

api:
	@if [ -f api/main.py ]; then uv run --extra rl uvicorn api.main:app --reload --port 8000; \
	else echo "api: api/main.py not implemented yet"; fi

web:
	@if [ -f frontend/package.json ]; then cd frontend && npm run dev -- --port 5173; \
	else echo "web: frontend/package.json not implemented yet"; fi

test:
	uv run pytest
	@if [ -f frontend/package.json ]; then cd frontend && npm test; fi

lint:
	uv run ruff check .
	uv run ruff format --check .

# Reproducible GPU training (decision 0051). Needs Docker + NVIDIA container toolkit.
CONFIG ?= config/train/smoke.yaml
SEED ?= 0
GPU_ID ?= 0
export GIT_SHA := $(shell git rev-parse HEAD 2>/dev/null)
export GIT_DIRTY := $(if $(shell git status --porcelain 2>/dev/null),true,false)

train-image:
	docker compose -f docker-compose.train.yml build train

train-image-verify:
	docker build -f docker/train.Dockerfile --target verify -t ppc-train:verify .

train-docker:
	GPU_ID=$(GPU_ID) docker compose -f docker-compose.train.yml run --rm train run $(CONFIG) --seed $(SEED) $(ARGS)

train-suite-docker:
	GPU_ID=$(GPU_ID) docker compose -f docker-compose.train.yml run --rm train suite $(SUITE) $(ARGS)

train-local:
	uv run --extra rl --extra train python -m model.experiments run $(CONFIG) --seed $(SEED) $(ARGS)

# Paper figures (docs/figures): pinned TeX Live in Docker, or local TeX with figures-local.
figures:
	docker build -f docker/tex.Dockerfile -t ppc-figures .
	docker run --rm -v "$(PWD)":/work -w /work ppc-figures

figures-local:
	bash docs/figures/build.sh
