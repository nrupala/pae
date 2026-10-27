.PHONY: all build test clean dev bundle demo seed

all: build

build: build-engine build-analytics build-ui

build-engine:
	cd engine && cargo build --release

build-analytics:
	cd analytics && pip install -e ".[dev]"

build-ui:
	cd ui && npx tsc

test: test-engine test-analytics

test-engine:
	cd engine && cargo test

test-analytics:
	cd analytics && python -m pytest tests/ -v

dev-engine:
	cd engine && cargo watch -x run

dev-ui:
	cd ui && npx live-server dist/

# Native release bundle (one per OS/arch): engine binary + frozen
# analytics + static UI + launcher + installers. See docs/PACKAGING.md.
bundle:
	scripts/build-bundle.sh

# Self-host demo: build the bundle, install it into a scratch prefix
# under dist-bundle/, start it with a scratch PAE_HOME, and seed the
# fictional demo data. Stop it with the printed command.
demo:
	scripts/build-bundle.sh
	./dist-bundle/pae-*/install.sh --prefix $(CURDIR)/dist-bundle/demo-prefix --bin-dir $(CURDIR)/dist-bundle/demo-bin
	PAE_HOME=$(CURDIR)/dist-bundle/demo-data ./dist-bundle/demo-prefix/pae start
	PAE_HOME=$(CURDIR)/dist-bundle/demo-data ./dist-bundle/demo-prefix/pae seed
	@echo ""
	@echo "Demo running at http://localhost:3001"
	@echo "Stop it with: PAE_HOME=$(CURDIR)/dist-bundle/demo-data ./dist-bundle/demo-prefix/pae stop"

# Seed (or re-seed) demo data against a natively running stack
# (dev servers or an installed bundle). Ports match the launcher
# defaults; override PAE_ENGINE_URL / PAE_ANALYTICS_URL if needed.
seed:
	PAE_ENGINE_URL=$${PAE_ENGINE_URL:-http://localhost:3001} PAE_ANALYTICS_URL=$${PAE_ANALYTICS_URL:-http://localhost:3002} python3 scripts/seed_demo.py

clean:
	cd engine && cargo clean
	rm -rf analytics/dist analytics/build analytics/*.egg-info
	rm -rf ui/dist
	rm -rf dist-bundle
