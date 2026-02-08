SHELL := /bin/bash
.PHONY: clean build test lint

PACKAGE_DIR := build/package
ZIP_FILE := build/s3-hydration-lambda.zip

clean:
	rm -rf build/

build: clean
	mkdir -p $(PACKAGE_DIR)
	pip install -r requirements.txt -t $(PACKAGE_DIR) --quiet
	cp -r src/ $(PACKAGE_DIR)/src/
	cd $(PACKAGE_DIR) && zip -r ../../$(ZIP_FILE) . -x '*.pyc' '__pycache__/*'
	@echo "Built $(ZIP_FILE)"
	@ls -lh $(ZIP_FILE)

test:
	python -m pytest tests/ -v

lint:
	python -m py_compile src/handler.py
	python -m py_compile src/transfer.py
	python -m py_compile src/config.py
	python -m py_compile src/metrics.py
	python -m py_compile src/exceptions.py
