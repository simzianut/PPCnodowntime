.PHONY: db migrate db-down seed data train api web test lint

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
	@if [ -f api/main.py ]; then uv run uvicorn api.main:app --reload --port 8000; \
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
