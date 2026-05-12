.PHONY: all build test clean dev docker-build docker-up docker-down

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

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	cd engine && cargo clean
	rm -rf analytics/dist analytics/build analytics/*.egg-info
	rm -rf ui/dist
