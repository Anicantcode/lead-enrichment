.PHONY: install demo preview enrich test lint format clean big

install:
	pip install --break-system-packages -e ".[dev]"

demo:
	lead-enrich demo -o ./out --format csv
	@echo "→ open out/summary.html"

preview:
	lead-enrich preview examples/sample_leads.csv --rows 5

enrich:
	lead-enrich enrich examples/sample_leads.csv -o ./out --format xlsx --verbose

test:
	pytest -q
	pytest --cov=src --cov-report=term-missing

lint:
	ruff check src/ tests/
	mypy src/ || true

format:
	ruff check --fix src/ tests/ || true

big:
	python examples/generate_big_file.py --rows 2000 --out /tmp/big_leads.csv
	lead-enrich enrich /tmp/big_leads.csv -o /tmp/out_big --format csv --concurrency 30
	ls -lh /tmp/out_big/

clean:
	rm -rf out out_xlsx /tmp/out_* __pycache__ .pytest_cache .ruff_cache
	find . -name "*.pyc" -delete
