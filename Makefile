
# Python dependencies helper
# --------------------------

pip-install:
	pip install -U pip poetry
	poetry install

requirements.txt:
	# exporting from poetry regulates inclusion of "dev" packages
	poetry export --without-hashes -f requirements.txt > requirements.txt

pip-requirements:
	rm -f requirements.txt
	make requirements.txt

pip-lock:
	poetry lock
	make pip-requirements

pip-update:
	# Update pip and poetry version
	pip install -U pip poetry

	# Update project packages
	poetry update

	make pip-requirements


# Git hooks helper
# ----------------

pc-install:
	pre-commit install

pc-uninstall:
	pre-commit uninstall

pc-update:
	pre-commit autoupdate

pc-run:
	pre-commit run --all-files
