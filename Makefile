.PHONY: help build up down logs test lint clean restart status \
       monitoring-up gateway-up full-up test-coverage test-integration showcase eval

help:
	@echo ""
	@echo "  AI Config Tool - Development Commands"
	@echo "  ────────────────────────────────────────"
	@echo ""
	@echo "  Core:"
	@echo "    make build          Build Docker images"
	@echo "    make up             Start core services"
	@echo "    make down           Stop all services"
	@echo "    make restart        Restart all services"
	@echo "    make logs           Follow service logs"
	@echo "    make status         Show service status"
	@echo ""
	@echo "  Profiles:"
	@echo "    make monitoring-up  Start with Prometheus + Grafana"
	@echo "    make gateway-up     Start with Nginx API gateway"
	@echo "    make full-up        Start all services + monitoring + gateway"
	@echo ""
	@echo "  Quality:"
	@echo "    make test           Run all unit tests"
	@echo "    make test-coverage  Run tests with coverage report"
	@echo "    make test-integration  Run integration test suite"
	@echo "    make eval           Score the bot on labeled requests against a real Ollama"
	@echo "    make lint           Run code linting (ruff)"
	@echo "    make showcase       Run feature showcase"
	@echo ""
	@echo "  Cleanup:"
	@echo "    make clean          Remove containers, images, and caches"
	@echo ""

build:
	docker compose build

up:
	docker compose up -d
	@echo "Services starting..."
	@sleep 5
	@$(MAKE) status

down:
	docker compose --profile monitoring --profile gateway down

restart:
	docker compose restart

logs:
	docker compose logs -f

status:
	@docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

monitoring-up:
	docker compose --profile monitoring up -d
	@echo ""
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana:    http://localhost:3000  (admin/admin)"
	@echo ""

gateway-up:
	docker compose --profile gateway up -d
	@echo ""
	@echo "  API Gateway: http://localhost:80"
	@echo ""

full-up:
	docker compose --profile monitoring --profile gateway up -d
	@echo ""
	@echo "  Services:    http://localhost:7000"
	@echo "  API Gateway: http://localhost:80"
	@echo "  Prometheus:  http://localhost:9090"
	@echo "  Grafana:     http://localhost:3000  (admin/admin)"
	@echo ""

test:
	@echo "Running schema-server tests..."
	@cd schema-server && python3 -m pytest tests/ -v
	@echo ""
	@echo "Running values-server tests..."
	@cd values-server && python3 -m pytest tests/ -v
	@echo ""
	@echo "Running bot-server tests..."
	@cd bot-server && python3 -m pytest tests/ -v
	@echo ""
	@echo "Running cli tests..."
	@cd cli && python3 -m pytest tests/ -v
	@echo ""
	@echo "All tests passed!"

test-coverage:
	@echo "Running tests with coverage..."
	@cd schema-server && python3 -m pytest tests/ -v --cov=. --cov-report=term-missing --junitxml=test-results.xml
	@echo ""
	@cd values-server && python3 -m pytest tests/ -v --cov=. --cov-report=term-missing --junitxml=test-results.xml
	@echo ""
	@cd bot-server && python3 -m pytest tests/ -v --cov=. --cov-report=term-missing --junitxml=test-results.xml
	@echo ""
	@cd cli && python3 -m pytest tests/ -v --cov=. --cov-report=term-missing --junitxml=test-results.xml

test-integration:
	@bash test_integration.sh

eval:
	@python3 evals/run.py --set all

showcase:
	@bash showcase.sh

lint:
	@echo "Linting schema-server..."
	@ruff check schema-server/
	@echo ""
	@echo "Linting values-server..."
	@ruff check values-server/
	@echo ""
	@echo "Linting bot-server..."
	@ruff check bot-server/
	@echo ""
	@echo "Linting cli..."
	@ruff check cli/
	@echo "Linting evals..."
	@ruff check evals/
	@echo ""
	@echo "All lint checks passed!"

clean:
	docker compose --profile monitoring --profile gateway down -v --rmi local
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".venv" -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".coverage" -delete 2>/dev/null || true
	@find . -name "test-results.xml" -delete 2>/dev/null || true
	@find . -name "coverage.xml" -delete 2>/dev/null || true
	@echo "Cleanup complete"
