.PHONY: setup build start demo dev test deploy-modal clean

PY ?= python3

setup:            ## install backend + frontend dependencies
	$(PY) -m pip install -e ".[modal,dev]"
	cd frontend && npm install

build:            ## build the web UI into frontend/dist
	cd frontend && npm run build

start: build      ## build the UI and serve everything on http://localhost:8000
	$(PY) -m evalplatform

demo: build       ## same, but force the simulated provider (no GPU / credentials)
	EVAL_DEFAULT_PROVIDER=mock $(PY) -m evalplatform

dev:              ## API with auto-reload + Vite dev server on :5173
	$(PY) -m evalplatform --reload & cd frontend && npm run dev

test:             ## run the backend test-suite
	$(PY) -m pytest -q

deploy-modal:     ## (optional) pre-deploy the GPU serving app; the platform also does this on first use
	$(PY) -m modal deploy modal_app/serve.py

clean:
	rm -rf frontend/dist data .pytest_cache
