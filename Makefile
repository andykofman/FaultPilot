PYTHON ?= $(shell if [ -x ./env/bin/python3 ]; then echo ./env/bin/python3; else echo python3; fi)

.PHONY: test doctor launch-help

test:
	$(PYTHON) -m pytest tests/

doctor:
	./scripts/doctor.sh

launch-help:
	./scripts/launch.sh help
