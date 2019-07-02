#!/usr/bin/env python3
"""Tools for GitHub interaction."""
import argparse
import os

from urllib.parse import urljoin

import github3
import github3.users
from qecommon_tools import var_from_env
import requests


GH_URL = "https://github.rackspace.com"
GH_TOKEN_ENV_KEY = "GH_TOKEN"


def _get_credentials(token=None):
    token = token or os.getenv(GH_TOKEN_ENV_KEY, None)
    assert token, "Required GH access token value not provided!"
    return {"token": token}


def assign_pr(owner, repo, pr_number, users, token=None, keep_current=True):
    """
    Assign PR to a user.

    Args:
        owner (str): name of user or org that owns the repo containing the PR
        repo (str): name of the repo containing the PR
        users (iterable): iterable of ssos for users to be assigned
        token (Optional[str]): token to authenticate to GitHub.
            If not provided, the `GH_TOKEN` environment variable will be checked.
        keep_current (bool): if True, leave current assignees in place,
            otherwise remove and assign *only* provided users.
    """
    creds = _get_credentials(token=token)
    gh = github3.enterprise_login(url=GH_URL, **creds)
    repo = gh.repository(owner, repo)
    pr = repo.issue(pr_number)
    current_assignees = {u.login for u in pr.assignees} if keep_current else set()
    pr.edit(assignees=list(current_assignees.union(users)))


def assign_pr_cli():
    """Handle CLI calls to assign_pr."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "owner", help="The name of the repository's owner/organization."
    )
    parser.add_argument("repo", help="The name of the repository.")
    parser.add_argument("pr_number", type=int, help="The PR Number to be assigned")
    parser.add_argument("users", nargs="+", help="The usernames to assign.")
    parser.add_argument(
        "--token",
        help=(
            "The GH access token can also be set "
            f'as "{GH_TOKEN_ENV_KEY}" environment variable.'
        ),
    )
    parser.add_argument(
        "--clear-current",
        action="store_false",
        dest="keep_current",
        help="Clear current assignees while adding provided users.",
    )
    args = parser.parse_args()
    assign_pr(
        args.owner,
        args.repo,
        args.pr_number,
        args.users,
        token=args.token,
        keep_current=args.keep_current,
    )


def post_docs_link(token=None, doc_path="HTMLReport"):
    """
    Post a docs link back to a PR from within the PR Checker Jenkins job.

    Args:
        token (str): GitHub access token
        doc_path (str): the subpath from the job link where the docs can be found
    """
    repo = ghprb_info.repository
    pull_id = ghprb_info.pull_request_id
    domain = ghprb_info.domain
    gh = GHPRSession(token, domain, repo, pull_id)

    report_url = f"{var_from_env('BUILD_URL')}/{doc_path}"
    gh.post_comment(f"Docs Link: {report_url}")


def post_docs_link_cli():
    """Handle CLI calls for post_docs_link."""
    parser = get_github_commenter_parser("Docs Link PR Commenter")
    parser.add_argument(
        "--doc-path",
        default="HTMLReport",
        help="subpath from the Jenkins Job URL where the docs are located",
    )
    args = parser.parse_args()

    post_docs_link(token=args.token, doc_path=args.doc_path)


class GHPRSession(requests.Session):
    """A GitHub session for managing a Pull Request."""

    base_url = None

    def __init__(self, token, domain, repo, pull_id):
        super(GHPRSession, self).__init__()
        self.headers.update({"Authorization": "token {}".format(token)})
        self._domain = domain
        self._repo = repo
        self._pull_id = pull_id
        self.base_url = self._base_url(domain, repo, pull_id)

    def _base_url(self, domain, repo, pull_id):
        # In repos without an active Issues section,
        # the Issue ID and PR ID *should* match,
        # but we will always positively grab the issue link from the PR
        # to prevent mis-commenting
        pull_data = self.get(
            "https://{}/api/v3/repos/{}/pulls/{}".format(domain, repo, pull_id)
        ).json()
        # ensure a single trailing slash to support proper urljoin
        return pull_data.get("issue_url").rstrip("/") + "/"

    def request(self, method, url, *args, **kwargs):
        """Place request."""
        url = urljoin(self.base_url, url)
        response = super(GHPRSession, self).request(method, url, *args, **kwargs)
        response.raise_for_status()
        return response

    def post_comment(self, comment_body):
        """Post Comment to PR."""
        return self.post("comments", json={"body": comment_body})


class _GitHubPRBInfo(object):
    """A class for getting GitHub Pull Request Builder related Jenkins env variables."""

    @property
    def repository(self):
        return var_from_env("ghprbGhRepository")

    @property
    def pull_request_id(self):
        return var_from_env("ghprbPullId")

    @property
    def domain(self):
        return var_from_env("ghprbPullLink").strip("https://").split("/")[0]


ghprb_info = _GitHubPRBInfo()
"""
_GitHubPRBInfo: An object that dynamically gets GHPRB Plugin
related Jenkins env variables.
"""


def get_github_commenter_parser(name="GitHub Pull Request Commenter"):
    """Build parser for GH Comment CLI."""
    parser = argparse.ArgumentParser(name)
    parser.add_argument(
        "token", help="GitHub Personal Access Token for commenting user"
    )
    return parser
