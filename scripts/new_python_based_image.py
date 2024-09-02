import argparse
import contextlib
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


LOGGER = logging.getLogger(__name__)


def configure_logger(log_level: str) -> None:
    """
    Configures the logging settings based on the provided log level.

    Args:
        log_level: The logging level to set (e.g., 'INFO', 'DEBUG').
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(asctime)s: %(message)s",
        datefmt="%H:%M:%S"
    )
    LOGGER.setLevel(log_level)


@dataclass
class Args:
    """
    Class to encapsulate command-line arguments.
    """
    context_dir: str
    source: str
    target: str
    match: str
    log_level: str


def extract_input_args() -> Args:
    """
    Extracts and validates command-line arguments.

    Returns:
        An instance of the Args class containing parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Script to create a new Python-based image from an existing one.",
        usage="python script.py --context-dir <directory> --source <python_version_source> --target <python_version_target> --match <match> [--log-level <level>]"
    )

    parser.add_argument(
        "--context-dir",
        help="The directory to be the context for searching.")
    parser.add_argument(
        "--source",
        help="The Python version to base the new image from.")
    parser.add_argument(
        "--target",
        help="The Python version to be used in the new image.")
    parser.add_argument(
        "--match",
        help="The string to match with the paths to base the new image from.")
    parser.add_argument(
        "--log-level", default="INFO",
        help="Set the logging level. Default: %(default)s.")

    args = parser.parse_args()

    missing_args = [arg for arg, value in vars(args).items() if value is None]
    if missing_args:
        print(f"Missing required arguments: {', '.join(missing_args)}")
        parser.print_help()
        sys.exit(1)

    return Args(args.context_dir, args.source, args.target, args.match, args.log_level)


def extract_python_version(version: str) -> list[str]:
    """
    Extracts the major and minor version components from a Python version string.

    Args:
        version: The Python version string (e.g., '3.9').

    Returns:
        A list containing the major and minor version components of a Python version.
    """
    return version.split(".")[:2]


def check_python_version(version: str) -> None:
    """
    Validates the format of a Python version string.

    Args:
        version: The Python version string to validate.
    """
    if not re.match(r'^\d+\.\d+$', version):
        LOGGER.error(
            f"Invalid Python version format: '{version}'. Expected format is <major>.<minor> (e.g., '3.9').")
        sys.exit(1)


def check_target_python_version_installed(version: str) -> None:
    """
    Checks if the specified Python version is installed on the system.

    Args:
        version: The Python version to check.
    """
    python_executable = f"python{version}"
    if shutil.which(python_executable) is None:
        LOGGER.error(f"Python {version} is not installed.")
        sys.exit(1)


def check_input_versions_not_equal(source_version: str, target_version: str) -> None:
    """
    Ensures that the source and target Python versions are different.

    Args:
        source_version: The source Python version.
        target_version: The target Python version.
    """
    if source_version == target_version:
        LOGGER.error("Source and target Python versions must be different.")
        sys.exit(1)


def check_os_linux() -> None:
    """
    Checks if the script is being run on a Linux operating system.
    """
    LOGGER.debug(f"Operating system: {platform.system()}")
    if platform.system() != "Linux":
        LOGGER.error(
            "This script can only be run on a Linux operating system.")
        sys.exit(1)


def check_pipenv_installed() -> None:
    """
    Checks if `pipenv` is installed on the system.
    """
    if shutil.which("pipenv") is None:
        LOGGER.error("pipenv is not installed.")
        sys.exit(1)


def check_requirements(args: Args) -> None:
    """
    Performs various checks to ensure that all requirements are met.

    Args:
        args: An instance of the Args class containing the command-line arguments.
    """
    check_os_linux()
    check_python_version(args.source)
    check_python_version(args.target)
    check_input_versions_not_equal(args.source, args.target)
    check_target_python_version_installed(args.target)
    check_pipenv_installed()


def find_matching_paths(context_dir: str, source_version: str, match: str) -> list[str]:
    """
    Finds directories in the context directory that match the specified source version and match criteria.

    Args:
        context_dir: The directory to search in.
        source_version: The Python version to match.
        match: The string to match with the paths.

    Returns:
        A list of directories that match the criteria and contain a Dockerfile.
    """
    blocklist = [os.path.join(".", path) for path in [".git",
                                                      ".github",
                                                      "ci",
                                                      "docs",
                                                      "manifests",
                                                      "scripts",
                                                      "tests"]]
    matching_paths = []
    processed_dirs = set()

    for root, dirs, files in os.walk(context_dir):
        if any(blocked in root for blocked in blocklist):
            LOGGER.debug(f"Skipping '{root}' - blocked directory")
            dirs[:] = []
            continue

        if source_version in root and match in root:
            if "Dockerfile" in files and root not in processed_dirs:
                LOGGER.debug(f"Found matching path with Dockerfile: '{root}'")
                matching_paths.append(root)
                processed_dirs.add(root)
            else:
                LOGGER.debug(f"Skipping match '{root}' - Dockerfile not found")
            dirs[:] = []

    return [p for p in matching_paths if source_version in p]


def replace_python_version_on_paths(paths_list: list, source_version: str, target_version: str) -> dict[str, str]:
    """
    Replaces occurrences of the source Python version with the target version in a list of paths.

    Args:
        paths_list: The list of paths to modify.
        source_version: The source Python version.
        target_version: The target Python version.

    Returns:
        A dictionary where keys are original paths and values are modified paths with the target version.
    """
    return {path: path.replace(source_version, target_version) for path in paths_list}


def copy_paths(paths_dict: dict) -> tuple[list[str], list[str]]:
    """
    Copies directories from source paths to destination paths.

    Args:
        paths_dict: A dictionary where keys are source paths and values are destination paths.

    Returns:
        A tuple of two lists for the successfully and failed copied paths.
    """
    success_paths = []
    failed_paths = []

    for src_path, dst_path in paths_dict.items():
        if os.path.exists(dst_path):
            LOGGER.debug(f"Path '{dst_path}' already exists.")
            failed_paths.append(dst_path)
        else:
            try:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                success_paths.append(dst_path)
                LOGGER.debug(f"Path '{src_path}' copied to {dst_path}")
            except Exception as e:
                LOGGER.error(
                    f"Error copying '{src_path}' to '{dst_path}': {e}")
                failed_paths.append(dst_path)

    return success_paths, failed_paths


def replace_python_version_in_file(file_path: str, source_version: str, target_version: str) -> None:
    """
    Replaces occurrences of the source Python version with the target version in a file.

    Args:
        file_path: The path to the file.
        source_version: The source Python version.
        target_version: The target Python version.
    """
    LOGGER.debug(f"Replacing Python versions in '{file_path}'")

    try:
        with open(file_path, "r") as file:
            content = file.read()

        content = replace_python_version_in_content(
            content, source_version, target_version)

        with open(file_path, "w") as file:
            file.write(content)
    except Exception as e:
        LOGGER.debug(f"Error replacing Python versions in '{file_path}': {e}")


def replace_python_version_in_content(content: str, source_version: str, target_version: str) -> str:
    """
    Replaces occurrences of the source Python version with the target version in a content string.

    Args:
        content: The content to modify.
        source_version: The source Python version.
        target_version: The target Python version.

    Returns:
        The modified content with the target Python version.
    """
    source_major, source_minor = extract_python_version(source_version)
    target_major, target_minor = extract_python_version(target_version)

    # Example: 3.9 -> 3.11
    result = content.replace(source_version,
                             target_version)

    # Example: 3-9 -> 3-11
    result = result.replace(
        f"{source_major}-{source_minor}",
        f"{target_major}-{target_minor}")

    # Example: python-39 -> python-311
    result = result.replace(f"python-{source_major}{source_minor}",
                            f"python-{target_major}{target_minor}")

    # Example: py39 -> py-311
    result = result.replace(f"py{source_major}{source_minor}",
                            f"py{target_major}{target_minor}")

    return result


def dict_to_str(dictionary: dict, enumerate_lines=False) -> str:
    """
    Converts a dictionary to a string representation.

    Args:
        dictionary: The dictionary to convert.
        enumerate_lines: Whether to enumerate lines in the output.

    Returns:
        The string representation of the dictionary.
    """
    if enumerate_lines:
        return '\n'.join(f"{i + 1}. '{k}' -> '{v}'" for i, (k, v) in enumerate(dictionary.items()))
    else:
        return '\n'.join(f"'{k}' -> '{v}'" for k, v in dictionary.items())


def list_to_str(lst: list, enumerate_lines=False) -> str:
    """
    Converts a list to a string representation.

    Args:
        lst: The list to convert.
        enumerate_lines: Whether to enumerate lines in the output.

    Returns:
        The string representation of the list.
    """
    if enumerate_lines:
        return "\n".join(f"{i + 1}. '{item}'" for i, item in enumerate(lst))
    else:
        return "\n".join(lst)


@contextlib.contextmanager
def logged_execution(title: str):
    """Usage:
            > with logged_execution("launching rockets"):
            > ...     result = launch_rockets()
    """
    LOGGER.info(f"{title}...")
    try:
        yield None
    finally:
        LOGGER.info(f"{title}... Done.")


def replace_version_in_directory(directory_path: str, source_version: str, target_version: str) -> str:
    """
    Replaces occurrences of the source Python version with the target version in file and directory names within a directory.

    Args:
        directory_path: The path to the directory.
        source_version: The source Python version.
        target_version: The target Python version.

    Returns:
        The path to the renamed file or directory.
    """
    LOGGER.debug(f"Replacing Python versions in '{directory_path}'")

    def rename_file_and_replace_python_version(path, filename, source_version, target_version, is_file=True) -> str:
        old_path = os.path.join(path, filename)
        new_filename = replace_python_version_in_content(filename,
                                                         source_version,
                                                         target_version)
        new_path = os.path.join(path, new_filename)

        if old_path != new_path:
            os.rename(old_path, new_path)
            LOGGER.debug(
                f"Renamed {'file' if is_file else 'directory'}: {old_path} -> {new_path}")

        if is_file:
            replace_python_version_in_file(new_path,
                                           source_version,
                                           target_version)

        return new_path

    for root, dirs, files in os.walk(directory_path, topdown=False):
        for file_name in files:
            rename_file_and_replace_python_version(root,
                                                   file_name,
                                                   source_version,
                                                   target_version,
                                                   is_file=True)

        for dir_name in dirs:
            rename_file_and_replace_python_version(root,
                                                   dir_name,
                                                   source_version,
                                                   target_version,
                                                   is_file=False)


def process_paths(copied_paths: list, source_version: str, target_version: str) -> tuple[list[str], list[str]]:
    """
    Processes the list of copied paths by replacing Python versions and running `pipenv lock` on Pipfiles.

    Args:
        copied_paths: The list of copied paths to process.
        source_version: The source Python version.
        target_version: The target Python version.

    Returns:
        A tuple of two lists for the successfully and failed processed lock files.
    """
    success_processed = []
    failed_processed = []
    for path in copied_paths:
        if not os.path.exists(path):
            LOGGER.warning(f"The path '{path}' does not exist.")
            continue

        replace_version_in_directory(path, source_version, target_version)
        success, failed = process_pipfiles(path, target_version)
        success_processed.extend(success)
        failed_processed.extend(failed)
    return success_processed, failed_processed


def process_pipfiles(path: str, target_version: str) -> tuple[list[str], list[str]]:
    """
    Processes Pipfiles in a given path by running `pipenv lock` on them.

    Args:
        path: The path to search for Pipfiles.
        target_version: The target Python version to use with `pipenv lock`.

    Returns:
        A tuple of two lists for the successfully and failed processed lock files.
    """
    success_processed = []
    failed_processed = []
    for root, _, files in os.walk(path):
        for file_name in files:
            if file_name.startswith("Pipfile") and "lock" not in file_name:
                pipfile_path = os.path.join(root, file_name)
                result = run_pipenv_lock(pipfile_path, target_version)
                (success_processed if result else failed_processed).append(pipfile_path)
    return success_processed, failed_processed


def run_pipenv_lock(pipfile_path: str, target_version: str) -> bool:
    """
    Runs `pipenv lock` for a specified Pipfile to generate a new lock file.

    Args:
        pipfile_path: The path to the Pipfile.
        target_version: The target Python version to use with `pipenv lock`.

    Returns:
        Whether the `pipenv lock` command was successful or not.
    """
    LOGGER.info(f"Running pipenv lock for '{pipfile_path}'")
    env = os.environ.copy()
    env["PIPENV_PIPFILE"] = os.path.basename(pipfile_path)

    try:
        result = subprocess.run(
            ["pipenv", "lock", "--python", target_version],
            cwd=os.path.dirname(pipfile_path),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            encoding="utf-8"
        )
        LOGGER.debug(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        LOGGER.debug(e.stderr)
        return False


def manual_checks() -> list[str]:
    """
    Provides a list of manual checks to perform after the script execution.

    Returns:
        A list of manual checks to review.
    """
    return [
        "Check the issues thrown during the script execution, if any, and fix them manually.",
        "Check if the Python version replacements have been performed correctly on the new files.",
        "Review and make the appropriate changes in the Makefile and CI-related files.",
        "Push the changes to a new branch on your fork to build the new images with GitHub workflows.",
        "Test the new images."
    ]


def main():
    args = extract_input_args()
    configure_logger(args.log_level)
    LOGGER.info(args)

    with logged_execution("Checking requirements"):
        check_requirements(args)

    with logged_execution(f"Finding matching paths with '{args.match}' and Python {args.source}"):
        paths = find_matching_paths(args.context_dir, args.source, args.match)

    paths_dict = replace_python_version_on_paths(paths,
                                                 args.source,
                                                 args.target)

    if len(paths_dict) == 0:
        LOGGER.info("No paths found to copy.")
        sys.exit(1)

    LOGGER.info(
        f"New folder(s) based on the input args:\n{dict_to_str(paths_dict, enumerate_lines=True)}")

    with logged_execution(f"Trying to copy {len(paths_dict)} folder(s)"):
        success_copied_paths, failed_copied_paths = copy_paths(paths_dict)

    LOGGER.info(
        f"{len(success_copied_paths)} folder(s) have been copied successfully whereas {len(failed_copied_paths)} failed.")

    exit_code = 0

    if len(failed_copied_paths) > 0:
        LOGGER.warning(
            f"Failed to copy the following folder(s):\n{list_to_str(failed_copied_paths, enumerate_lines=True)}")
        exit_code = 1

    if len(success_copied_paths) > 0:
        with logged_execution("Processing copied folders"):
            _, failed_processed = process_paths(success_copied_paths,
                                                args.source,
                                                args.target)

        if len(failed_processed) > 0:
            LOGGER.warning(
                f"Failed to process the following lock files:\n{list_to_str(failed_processed, enumerate_lines=True)}")
            exit_code = 1

    LOGGER.info(
        f"Manual checks to perform after the script execution:\n{list_to_str(manual_checks(), enumerate_lines=True)}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
