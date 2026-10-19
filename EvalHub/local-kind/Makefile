MODE ?= mock
export MODE

.PHONY: local-env-all local-env-doctor local-env-build-load local-env-render local-env-up local-env-test local-env-smoke local-env-godog local-env-logs local-env-forward local-env-secret local-env-down local-env-reset check
local-env-all:
	$(MAKE) check
	$(MAKE) local-env-doctor
	$(MAKE) local-env-build-load
	$(MAKE) local-env-up
	$(MAKE) local-env-test

local-env-doctor:
	bash ./local-env.sh doctor
local-env-build-load:
	bash ./local-env.sh build-load
local-env-render:
	bash ./local-env.sh render
local-env-up:
	bash ./local-env.sh up
local-env-test:
	$(MAKE) local-env-smoke
	$(MAKE) local-env-godog
local-env-smoke:
	bash ./local-env.sh smoke
local-env-godog:
	bash ./local-env.sh godog
local-env-logs:
	bash ./local-env.sh logs
local-env-forward:
	bash ./local-env.sh forward
local-env-secret:
	bash ./local-env.sh secret
local-env-down:
	bash ./local-env.sh down
local-env-reset:
	bash ./local-env.sh reset --confirm-reset "$${CONFIRM_RESET:-}"
check:
	bash -n ./local-env.sh
	python3 -m unittest discover -s tests -v
