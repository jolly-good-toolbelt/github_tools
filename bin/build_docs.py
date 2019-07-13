#! /usr/bin/env python

"""Build the documentation for this package."""
import argparse
import glob
import os
import shutil
from subprocess import check_call
from subprocess import check_output


# Assumption: This script lives in a directory
# that is one level down from the root of the repo:
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# NOTE: 'git rev-parse --show-toplevel' would work from anywhere.
#       We are doing git commands "anyways", but I am not sure how much git-ness we want
#       to bake into this script...

DOCS_OUTPUT_DIRECTORY = "docs"
DOCS_WORKING_DIRECTORY = "_docs"


def main():
    """Build the docs."""
    # Setup environment variables
    commit_id = check_output(
        ["git", "rev-parse", "HEAD"], cwd=BASE_DIR, universal_newlines=True
    )
    os.environ["GIT_COMMIT_ID"] = commit_id.rstrip("\n")

    origin_url = check_output(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=BASE_DIR,
        universal_newlines=True,
    )
    os.environ["GIT_ORIGIN_URL"] = origin_url.rstrip("\n")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the output directory before starting to build documents",
    )
    args = parser.parse_args()

    if args.clean:
        shutil.rmtree(DOCS_OUTPUT_DIRECTORY, ignore_errors=True)
        shutil.rmtree(DOCS_WORKING_DIRECTORY, ignore_errors=True)

    sphinx_apidoc_cmd = [
        "poetry",
        "run",
        "sphinx-apidoc",
        "--output-dir",
        DOCS_WORKING_DIRECTORY,
        "--no-toc",
        "--force",
        "--module-first",
    ]
    print("Building github_tools API docs")
    check_call(sphinx_apidoc_cmd + ["github_tools"], cwd=BASE_DIR)

    # Copy over all the top level rST files so we don't
    # have to keep a duplicate list here.
    for filename in glob.iglob("*.rst"):
        shutil.copy(filename, DOCS_WORKING_DIRECTORY)

    for filename in glob.iglob(os.path.join("sphinx_docs", "*")):
        shutil.copy(filename, DOCS_WORKING_DIRECTORY)

    os.environ["PYTHONPATH"] = os.path.curdir
    check_call(
        [
            "poetry",
            "run",
            "sphinx-build",
            "-c",
            DOCS_WORKING_DIRECTORY,
            "-aEW",
            DOCS_WORKING_DIRECTORY,
            DOCS_OUTPUT_DIRECTORY,
        ],
        cwd=BASE_DIR,
    )


if __name__ == "__main__":
    main()
