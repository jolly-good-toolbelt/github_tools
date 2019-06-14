#!/usr/bin/env python3
"""Tools for GitHub interaction."""
import argparse
import collections
import datetime
import email.mime.multipart
import email.mime.text
import os
import re
import shutil
import smtplib
import subprocess

from urllib.parse import urljoin

import github3
import github3.users
import qecommon_tools
from qecommon_tools import http_helpers
import requests


class UTC(datetime.tzinfo):
    """UTC TZInfo."""

    def utcoffset(self, dt):
        """Return offset for UTC."""
        return datetime.timedelta(0)

    def tzname(self, dt):
        """Return UTC TZ name."""
        return "UTC"

    def dst(self, dt):
        """Return DST offset."""
        return datetime.timedelta(0)


NOW = datetime.datetime.now(UTC())
PULL_WAIT = 20 * 60 * 60


GH_URL = "https://github.rackspace.com"
GH_TOKEN_ENV_KEY = "GH_TOKEN"


def get_reviews(token, organization, pr_age, name_filter=""):
    """
    Collect open reviews for a given org.

    Args:
        token (str): GitHub access token
        organization (str): Name of GitHub Organization to check
        pr_age (int): minimum PR Age (in seconds) to be included
        name_filter (Optional[str]): If provided, only check repos which belong
            to teams whose names start with the provided string.

    Returns:
        collections.defaultdict: a dict with keys of assignee, and values as tuples
        of assigned PR title and url.

    """
    reviews = collections.defaultdict(set)
    gh = github3.enterprise_login(token=token, url=GH_URL)
    org = gh.organization(organization)
    repos = set()
    for team in (x for x in org.teams() if x.name.startswith(name_filter)):
        repos.update(team.repositories())
    for repo in repos:
        for pull in (x for x in repo.pull_requests() if x.state == "open"):
            assignees = {x.login for x in pull.assignees}
            if not assignees:
                continue
            secs_since_last_update = (NOW - pull.updated_at).total_seconds()
            # Check the assignee list and ensure it is not solely the author.
            # In the case of ambiguity,
            # err on the side of caution and alert all parties involved.
            if {pull.user.login} != assignees and secs_since_last_update > pr_age:
                for assignee in assignees:
                    reviews[assignee].add((pull.title, pull.html_url))
    return reviews


def send_email(user, review_list):
    """
    Send an email to a given user noting PRs pending review.

    Args:
        user (str): sso of user to be emailed
        review_list (list): list of tuples of (title, url)
            for all assigned PRs of which to notify the user.
    """
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = "Pull Requests Needing Attention"
    msg["From"] = "rs-pr-checker@rackspace.com"
    # Get Name and address from Hozer, since it's not in GitHub
    req = requests.get("https://finder.rackspace.net/mini.php?q={}".format(user))
    for line in req.text.splitlines():
        match = re.match(
            "^<tr><td>(?P<name>.*?)</td>.*<td>(?P<email>.*?)</td></tr>$", line
        )
        if match:
            to_address = match.groupdict()["email"]
            msg["To"] = "{} <{}>".format(*match.groups())
    text = "The following Pull Requests need review:\n"
    html = "<html><body><p>The following Pull Requests need review:</p><ul>"
    for title, issue_url in review_list:
        text += "{} - {}\n".format(title, issue_url)
        html += '<li><a href="{}">{}</a></li>'.format(issue_url, title)
    html += "</ul></body></html>"
    msg.attach(email.mime.text.MIMEText(text, "plain"))
    msg.attach(email.mime.text.MIMEText(html, "html"))

    s = smtplib.SMTP("smtp1.dfw1.corp.rackspace.com")
    s.sendmail(msg["From"], to_address, msg.as_string())
    s.quit()


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


def main(token, organization, name_filter, pr_age):
    """
    Run a check for open PRs and send notification emails.

    Args:
        token (str): GitHub access token
        organization (str): Name of GitHub Organization to check
        name_filter (str): Only check repos which belong
            to teams whose names start with the provided string.
        pr_age (int): minimum PR Age (in seconds) to be included
    """
    for user, review_list in get_reviews(
        token, organization, pr_age, name_filter=name_filter
    ).items():
        send_email(user, review_list)


def pr_checker():
    """Handle CLI calls to main."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--name-filter", help="Filter for team names, if needed", default=""
    )
    parser.add_argument("token", help="GitHub Token")
    parser.add_argument("organization", help="GitHub Organization")
    wait_help = "Time, in seconds, to check the PR age against"
    parser.add_argument("--pr-age", default=PULL_WAIT, help=wait_help)
    args = parser.parse_args()
    main(args.token, args.organization, args.name_filter, args.pr_age)


def _update_hooks(update_dir, force, source_hooks):
    """Find existing repositories and install hooks."""
    for dir_path, dir_names, file_names in os.walk(update_dir):
        if ".git" in dir_names:
            if force:
                existing_dir = os.path.join(dir_path, ".git", "hooks")
                for existing_hook in source_hooks.intersection(
                    os.listdir(existing_dir)
                ):
                    os.remove(os.path.join(existing_dir, existing_hook))
            qecommon_tools.safe_run(["git", "init"], cwd=dir_path)


def install_hooks():
    """Handle CLI calls to install repo hooks."""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Install helpful git hook templates",
    )
    update_help = (
        "Optional list of directories to scan for git projects and update the hooks."
    )
    parser.add_argument("update_paths", nargs="*", help=update_help)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override git hooks in existing git projects",
    )
    parser.add_argument(
        "--template-path",
        default="~/.git-templates",
        help="Path to install hook templates",
    )
    args = parser.parse_args()
    # Update git config for template path
    config_command = [
        "git",
        "config",
        "--global",
        "init.templatedir",
        args.template_path,
    ]
    qecommon_tools.safe_run(config_command)
    # Create necessary directories
    destination_dir = os.path.expanduser(os.path.join(args.template_path, "hooks"))
    if not os.path.exists(destination_dir):
        os.makedirs(destination_dir)
    # Copy hooks to template directory
    source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks")
    source_hooks = set(os.listdir(source_dir))
    for source_hook in source_hooks:
        shutil.copy(os.path.join(source_dir, source_hook), destination_dir)
    # (Optionally) Update any git repositories found in the provided path(s)
    for update_dir in args.update_paths:
        _update_hooks(update_dir, args.force, source_hooks)


def is_changed_diff_line(line):
    """
    Determine whether or not a give line is a changed line within a git diff string.

    All lines that are actually changed start with a ``+`` or a ``-``.
    File changes start with ``+++`` or ``---``.

    Args:
        line (str): The line to check.

    Returns:
        bool: Whether or not the line is a changed line.

    """
    return line.startswith(("+", "-"))


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

    def get_diff(self, base_branch="master", files="", only_changed_lines=False):
        """
        Get the diff of the PR.

        Args:
            base_branch (str): The branch against which the PR is based.
            files (Union[str, list]): The specific files for which to get the diff.
            only_changed_lines (bool): Whether or not to return only the lines
                that were actually changed, omitting other diff related information.

        Returns:
            str: The diff of the PR.

        """
        # Get the latest commit to the base branch
        response = self.get(
            "https://{}/api/v3/repos/{}/branches/{}"
            "".format(self._domain, self._repo, base_branch)
        )
        http_helpers.validate_response_status_code(200, response)
        latest_base_branch_commit = response.json()["commit"]["sha"]

        # Get the requested diff for the PR
        if isinstance(files, list):
            files = " ".join(files)
        # Assumes your git is checked out to the latest PR commit
        diff_command = "git diff --diff-filter=ACMRT {} HEAD {}" "".format(
            latest_base_branch_commit, files
        )
        diff = subprocess.check_output(diff_command.split()).decode()

        # Replace the escaped newlines so the return string format is as expected.
        diff = diff.replace("\\n", "\n")

        if only_changed_lines:
            # Only include lines that were actually changed
            return qecommon_tools.filter_lines(is_changed_diff_line, diff)

        return diff


class _GitHubPRBInfo(object):
    """A class for getting GitHub Pull Request Builder related Jenkins env variables."""

    @property
    def repository(self):
        return qecommon_tools.var_from_env("ghprbGhRepository")

    @property
    def pull_request_id(self):
        return qecommon_tools.var_from_env("ghprbPullId")

    @property
    def domain(self):
        return (
            qecommon_tools.var_from_env("ghprbPullLink").strip("https://").split("/")[0]
        )


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


if __name__ == "__main__":
    pr_checker()
