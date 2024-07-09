import yaml
import re, os
from packaging import version
from github import Github, Repository
from github.Repository import Repository
import subprocess

class AutowareRepos:
    """
    This class gets information from autoware.repos and updates it

    Attributes:
        autoware_repos_path (str): the path to autoware.repos. e.g. "./autoware.repos"
        autoware_repos (dict): the content of autoware.repos
    """
    def __init__(self, autoware_repos_path: str):
        self.autoware_repos_path: str = autoware_repos_path
        with open(self.autoware_repos_path, "r") as file:
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

        with open(self.autoware_repos_path, "w") as file:
            yaml.safe_dump(self.autoware_repos, file)

class GitHubInterface:
    def __init__(self, token: str):
        self.g = Github(token)

    def url_to_repository_name(self, url:str) -> str:
        # get repository name from url
        pattern = r'https://github.com/([^/]+/[^/]+)\.git'
        match = re.search(pattern, url)
        assert match is not None, f"URL {url} is invalid"
        repo_name = match.group(1)

        return repo_name

    def repository_tags(self, url:str) -> list[str]:
        repo_name = self.url_to_repository_name(url)
        repo: Repository = self.g.get_repo(repo_name)
        tags = repo.get_tags()

        return [tag.name for tag in tags]

    def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str) -> None:
        repo: Repository = self.g.get_repo(repo_name)

        # Create a PR from head to base
        repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
        )

def get_latest_tag(tags: list[str], current_version: str) -> str:
    latest_tag = current_version
    for tag in tags:
        if version.parse(tag.name) > version.parse(current_version):
            latest_tag = tag.name
    return latest_tag

def create_branch_with_new_version(url:str, repo_name: str, latest_tag: str, autoware_repos: AutowareRepos) -> None:
    # create a new branch
    subprocess.run(["git", "switch", "-c", f"feat/update-{repo_name}"])

    # change version in autoware.repos
    autoware_repos.update_repository_version(url, latest_tag)
    # add
    subprocess.run(["git", "add", "autoware.repos"])
    # commit
    subprocess.run(["git", "commit", "-s", "-m", f"feat(autoware.repos): update {repo_name} to {latest_tag}"])
    # push
    subprocess.run(["git", "push", "origin", f"feat/update-{repo_name}"])

def create_version_update_pr():
    autoware_repos: AutowareRepos = AutowareRepos(autoware_repos_path = "./autoware.repos")
    repository_url_semantic_version_dict: dict[str, str] = autoware_repos.pickup_semver_respositories(semantic_version_pattern = r'(v\d+\.\d+\.\d+)')

    github_interface = GitHubInterface(token = os.getenv("GITHUB_TOKEN"))

    # autoware_repo_name = "autowarefoundation/autoware"
    autoware_repo_name = "tier4/experimental_automated_tag_tracer"
    autoware_base_branch: str = "main"

    for url, current_version in repository_url_semantic_version_dict.items():

        # get tags of the repository
        tags: list[str] = github_interface.repository_tags(url)

        if current_version in tags:
            latest_tag: str = get_latest_tag(tags, current_version)

            repo_name: str = github_interface.url_to_repository_name(url)
            create_branch_with_new_version(url, repo_name, latest_tag, autoware_repos)

            github_interface.create_pull_request(
                repo_name = autoware_repo_name,
                title = f"feat(autoware.repos): update {repo_name} to {latest_tag}",
                body = "test PR",
                head = f"feat/update-{repo_name}",
                base = autoware_base_branch
            )

if __name__ == "__main__":
    create_version_update_pr()
