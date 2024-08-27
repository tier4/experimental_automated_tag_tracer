import os
import re
import argparse
import logging

import yaml
from packaging import version
import git
import github
from github import Github


# Parse arguments
parser = argparse.ArgumentParser(description="Create a PR to update version in autoware.repos")

# Verbosity count
parser.add_argument("-v", "--verbose", action="count", default=0, help="Verbosity level")

# Repository information
args_repo = parser.add_argument_group("Repository information")
args_repo.add_argument("--parent_dir", type=str, default="./", help="The parent directory of the repository")
args_repo.add_argument("--repo_name", type=str, default="tier4/experimental_automated_tag_tracer", help="The repository name to create a PR")
args_repo.add_argument("--base_branch", type=str, default="main", help="The base branch of autoware.repos")
args_repo.add_argument("--new_branch_prefix", type=str, default="feat/update-", help="The prefix of the new branch name")
args_repo.add_argument("--semantic_version_pattern", type=str, default=r'(v\d+\.\d+\.\d+)', help="The pattern of semantic version")

# For the Autoware
args_aw = parser.add_argument_group("Autoware")
args_aw.add_argument("--autoware_repos_file_name", type=str, default="autoware.repos", help="The path to autoware.repos")

args = parser.parse_args()


# Initialize logger depending on the verbosity level
if args.verbose == 0:
    logging.basicConfig(level=logging.WARNING)
elif args.verbose == 1:
    logging.basicConfig(level=logging.INFO)
elif args.verbose >= 2:
    logging.basicConfig(level=logging.DEBUG)


logger = logging.getLogger(__name__)


class AutowareRepos:
    """
    This class gets information from autoware.repos and updates it

    Attributes:
        autoware_repos_file_name (str): the path to autoware.repos. e.g. "./autoware.repos"
        autoware_repos (dict): the content of autoware.repos
    """
    def __init__(self, autoware_repos_file_name: str):
        self.autoware_repos_file_name: str = autoware_repos_file_name
        with open(self.autoware_repos_file_name, "r") as file:
            self.autoware_repos = yaml.safe_load(file)

    def _parse_repos(self) -> dict[str, str]:
        """
        parse autoware.repos and return a dictionary of GitHub repository URLs and versions

        Returns:
            repository_url_version_dict (dict[str, str]): a dictionary of GitHub repository URLs and versions. e.g. {'https://github.com/tier4/glog.git': 'v0.6.0'}
        """
        repository_url_version_dict: dict[str, str] = {
            repository_info["url"]: repository_info["version"]
            for repository_info in self.autoware_repos["repositories"].values()
        }
        return repository_url_version_dict

    def pickup_semver_respositories(self, semantic_version_pattern: str) -> dict[str, str]:
        """
        pick up repositories with semantic version tags

        Args:
            semantic_version_pattern (str): a regular expression pattern for semantic version. e.g. r'(v\d+\.\d+\.\d+)'

        Returns:
            repository_url_semantic_version_dict (dict[str, str]): a dictionary of GitHub repository URLs and semantic versions. e.g. {'https://github.com/tier4/glog.git': 'v0.6.0'}

        """
        repository_url_version_dict = self._parse_repos()

        repository_url_semantic_version_dict: dict[str, str] = {
            url: version
            for url, version in repository_url_version_dict.items()
            if re.search(semantic_version_pattern, version)
        }
        return repository_url_semantic_version_dict

    def update_repository_version(self, url: str, new_version: str) -> None:
        """
        update the version of the repository specified by the URL

        Args:
            url (str): the URL of the repository to be updated
            new_version (str): the new version to be set
        """
        for repository_relative_path, repository_info in self.autoware_repos["repositories"].items():
            if repository_info["url"] == url:
                target_repository_relative_path: str = repository_relative_path

        self.autoware_repos["repositories"][target_repository_relative_path]["version"] = new_version

        with open(self.autoware_repos_file_name, "w") as file:
            yaml.safe_dump(self.autoware_repos, file)


class GitHubInterface:

    # Pattern for GitHub repository URL
    URL_PATTERN = r'https://github.com/([^/]+/[^/]+)\.git'

    def __init__(self, token: str):
        self.g = Github(token)

    def url_to_repository_name(self, url:str) -> str:
        # Get repository name from url
        match = re.search(GitHubInterface.URL_PATTERN, url)
        assert match is not None, f"URL {url} is invalid"
        repo_name = match.group(1)

        return repo_name

    def get_tags_by_url(self, url: str) -> list[str]:
        # Extract repository's name from URL
        repo_name = self.url_to_repository_name(url)

        # Get tags
        tags: github.PaginatedList.PaginatedList = self.g.get_repo(repo_name).get_tags()

        return [tag.name for tag in tags]

    def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str) -> None:
        # Create a PR from head to base
        self.g.get_repo(repo_name).create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
        )


def get_latest_tag(tags: list[str], current_version: str) -> str:
    latest_tag = current_version
    for tag in tags:
        if version.parse(tag) > version.parse(current_version):
            latest_tag = tag
    return latest_tag


def create_one_branch(repo: git.Repo, branch_name: str) -> bool:

    # Check if the branch already exists
    if branch_name in repo.heads:
        logger.info(f"Branch '{branch_name}' already exists.")
        return False
    else:
        # Create a new branch and checkout
        repo.create_head(branch_name)
        logger.info(f"Created a new branch '{branch_name}'")
        return True


def create_version_update_pr(args: argparse.Namespace) -> None:

    # Get GitHub token
    github_token: str = os.getenv("GITHUB_TOKEN", default=None)
    if github_token == "None":
        raise ValueError("Please set GITHUB_TOKEN as an environment variable")
    github_interface = GitHubInterface(token = github_token)

    autoware_repos: AutowareRepos = AutowareRepos(autoware_repos_file_name = args.autoware_repos_file_name)

    # Get the repositories with semantic version tags
    repository_url_semantic_version_dict: dict[str, str] = autoware_repos.pickup_semver_respositories(semantic_version_pattern = args.semantic_version_pattern)

    # Get reference to the repository
    repo = git.Repo(args.parent_dir)

    # Remote branches
    branches = []
    for ref in repo.references:
        if isinstance(ref, git.refs.remote.RemoteReference):
            # Remove the 'origin/' prefix
            branch_name = ref.name.split('/', 1)[1]
            if branch_name not in branches:
                branches.append(branch_name)

    for url, current_version in repository_url_semantic_version_dict.items():
        '''
        Description:
            In this loop, the script will create a PR to update the version of the repository specified by the URL.
            The step is as follows:
                1. Get tags of the repository
                2. Check if the current version is the latest
                3. Get the latest tag
                4. Create a new branch
                5. Update autoware.repos
                6. Commit and push
                7. Create a PR
        '''

        # get tags of the repository
        tags: list[str] = github_interface.get_tags_by_url(url)

        if current_version in tags:

            latest_tag: str = get_latest_tag(tags, current_version)

            # Get repository name
            repo_name: str = github_interface.url_to_repository_name(url)

            # Set branch name
            branch_name: str = f"{args.new_branch_prefix}{repo_name}/{latest_tag}"

            # Check if the remote branch already exists
            if branch_name in branches:
                logger.info(f"Branch '{branch_name}' already exists on the remote.")
                continue

            # First, create a branch
            newly_created: bool = create_one_branch(repo, branch_name)

            # Switch to the branch
            repo.heads[branch_name].checkout()

            try:
                # Change version in autoware.repos
                autoware_repos.update_repository_version(url, latest_tag)

                # Add
                repo.index.add([args.autoware_repos_file_name])

                # Commit
                commit_message = f"feat(autoware.repos): update {repo_name} to {latest_tag}"
                repo.git.commit(m=commit_message, s=True)

                # Push
                origin = repo.remote(name='origin')
                origin.push(branch_name)

                # Switch back to base branch
                repo.heads[args.base_branch].checkout()

                github_interface.create_pull_request(
                    repo_name = args.repo_name,
                    title = f"feat(autoware.repos): update {repo_name} to {latest_tag}",
                    body = f"This PR updates the version of the repository {repo_name} in autoware.repos",
                    head = branch_name,
                    base = args.base_branch
                )
            except Exception as e:
                logger.error(f"Failed to create a PR: {e}")

                # Switch back to base branch
                repo.heads[args.base_branch].checkout()

                # Clean up if the branch looks created by this script
                if newly_created:
                    # Delete the branch if the PR creation failed
                    repo.delete_head(branch_name, force=True)
                    logger.info(f"Deleted branch {branch_name}")

            finally:

                # Switch back to base branch
                repo.heads[args.base_branch].checkout()

                # Reset any changes
                repo.git.reset('--hard', f'origin/{args.base_branch}')

                # Clean untracked files
                repo.git.clean('-fd')

                # Restore base's autoware.repos
                autoware_repos: AutowareRepos = AutowareRepos(autoware_repos_file_name = args.autoware_repos_file_name)
        else:
            logger.debug(f"Repository {url} has the latest version {current_version}. Skip for this repository.")


if __name__ == "__main__":

    create_version_update_pr(args)
