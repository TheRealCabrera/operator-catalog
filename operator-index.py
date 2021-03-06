#!/usr/bin/env python3
"""
operator-index.py

A simple script to build OLM catalog index images in CI tooling
"""

from typing import List, Iterable, TypeVar
from lastversion import lastversion
import yaml
import click
import requests
import sys
import subprocess
import shlex
import os
import os.path
import platform
import logging
import logging.handlers


if platform.system() != "Linux":
    raise RuntimeError("operator-index.py is designed only for Linux.")

config = {
    "opm_path": os.path.join(os.environ["HOME"], ".local", "bin", "opm"),
    "opm_url": "https://github.com/operator-framework/operator-registry/releases/download/v",  # noqa: E501
}

T = TypeVar("OperatorIndexSettings")


class OperatorBundle(object):
    """
    A simple class to keep track of our Operator Bundles from configuration
    """
    def __init__(self, name: str = None, img: str = None,
                 tag: str = None) -> None:
        self.name = name
        self.img = img
        self.tag = tag

    def __repr__(self) -> str:
        return "OperatorBundle(name={}, img={}, tag={})".format(
            self.name, self.img, self.tag
        )


class OperatorIndex(object):
    """
    A simple class to keep track of our Operator Index from configuration
    """
    def __init__(self, img: str = None, tag: str = None) -> None:
        self.img = img
        self.tag = tag

    def __repr__(self) -> str:
        return "OperatorIndex(img={}, tag={})".format(
            self.img, self.tag
        )


class OperatorIndexSettings(object):
    """
    A class that keeps track of our settings file. Includes bundle and index
    settings, as well as a method for loading from a file. Can build an OPM
    command line argument string from stored settings.
    """
    def __init__(self, bundles: List[OperatorBundle] = [],
                 index: OperatorIndex = None) -> None:
        self.bundles = bundles
        self.index = index

    def __repr__(self) -> str:
        return "OperatorIndexSettings(bundles={}, index={})".format(
            self.bundles, self.index
        )

    @classmethod
    def load(cls, file: str = "operator-index.yml") -> T:
        """
        Alternate constructor that loads the necessary bundle and index data
        from a properly structured yaml file.
        """
        with open(file) as f:
            settings = yaml.safe_load(f)
        return cls(
            bundles=[
                OperatorBundle(**bundle)
                for bundle in settings.get("operator_bundles")
            ],
            index=OperatorIndex(**settings.get("catalog_index"))
        )

    def generate_command_line(self) -> str:
        """
        Build the necessary command line arguments for the opm CLI to generate
        an index from these settings.
        """
        bundles = ",".join([
            ":".join([bundle.img, bundle.tag])
            for bundle in self.bundles
        ])
        index = "{}:{}".format(self.index.img, self.index.tag)
        logger.debug(
            "Generated bundles {} and index {}".format(bundles, index)
        )

        return " index add --build-tool {} --bundles {} --tag {}".format(
            self._determine_runtime(),
            bundles,
            index
        )

    @staticmethod
    def _determine_runtime() -> str:
        """
        Determine the container runtime that should be used by the opm CLI to
        build index images.
        """
        for line in shell("which docker", fail=False):
            logger.debug("LINE: {}".format(line))
            if line.endswith("/docker") and not os.path.islink(line):
                return "docker"
        for line in shell("which podman", fail=False):
            logger.debug("LINE: {}".format(line))
            if line.endswith("/podman") and not os.path.islink(line):
                return "podman"
        raise RuntimeError("Unable to identify a container runtime!")


def make_logger(verbosity: int = None):
    """
    Creates a logger in a dynamic way, allowing us to call it multiple times
    if needed.
    """
    logger = logging.getLogger('operator-index.py')
    logger.setLevel(logging.DEBUG)

    if len(logger.handlers) == 0:
        # A well-parsable format
        _format = '{asctime} {name} [{levelname:^9s}]: {message}'
        formatter = logging.Formatter(_format, style='{')

        stderr = logging.StreamHandler()
        stderr.setFormatter(formatter)
        if verbosity is not None:
            # Sets log level based on verbosity, verbosity=3 is the most
            #   verbose log level
            stderr.setLevel(40 - (min(3, verbosity) * 10))
        else:
            # Use WARNING level verbosity
            stderr.setLevel(40)
        logger.addHandler(stderr)

        if os.path.exists('/dev/log'):
            # Use the default syslog socket at INFO level
            syslog = logging.handlers.SysLogHandler(address='/dev/log')
            syslog.setFormatter(formatter)
            syslog.setLevel(logging.INFO)
            logger.addHandler(syslog)

    elif verbosity is not None:
        # We may have already created the logger, but without specifying
        #   verbosity. So here, we grab the stderr handler and set its
        #   verbosity level.
        stderr = logger.handlers[0]
        stderr.setLevel(40 - (min(3, verbosity) * 10))

    return logger


# Create a default logger with no verbosity set, for functions that need it.
logger = make_logger()


def utf8ify(line_bytes: List[bytes] = None) -> str:
    """
    Decodes type line_bytes input as utf-8 and strips excess whitespace from
    the end.
    """
    return line_bytes.decode("utf-8").rstrip()


def shell(cmd: str = None, fail: bool = True) -> Iterable[str]:
    """
    Runs a command in a subprocess, yielding lines of output from it and
    optionally failing with its non-zero return code.
    """
    logger.debug("Running: {}".format(cmd))
    proc = subprocess.Popen(shlex.split(cmd),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)

    for line in map(utf8ify, iter(proc.stdout.readline, b'')):
        yield line

    ret = proc.wait()
    if fail and ret != 0:
        logger.error("Command errored: {}".format(cmd))
        exit(ret)
    elif ret != 0:
        logger.info("Command returned error {}: {}".format(ret, cmd))


def install_opm(version: str = "latest") -> None:
    """
    Downloads the requested OPM binary from their releases page, saves it in
      the configured opm_path with a version identifier postpended, and
      symlinks into place.
    """
    if not os.path.isdir(os.path.dirname(config["opm_path"])):
        logger.debug("Creating directory: {}".format(
            os.path.dirname(config["opm_path"])
        ))
        os.mkdir(os.path.dirname(config["opm_path"]))
    if version == "latest":
        latest_opm = lastversion.latest(
            "operator-framework/operator-registry"
        )
        # Clean up root logger manipulation by lastversion
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        version = str(latest_opm)
        logger.debug("Identified latest version as: {}".format(version))

    if not os.path.exists(config["opm_path"] + "-" + version):
        logger.debug("Downloading {}".format(
            config["opm_url"] + version + "/linux-amd64-opm"
        ))
        r = requests.get(config["opm_url"] + version + "/linux-amd64-opm")
        logger.debug("Saving to {}".format(config["opm_path"] + "-" + version))
        with open(config["opm_path"] + "-" + version, "wb") as f:
            f.write(r.content)
        os.chmod(config["opm_path"] + "-" + version, 0o550)

        if os.path.islink(config["opm_path"]):
            logger.debug("Removing old symlink")
            os.remove(config["opm_path"])
        elif os.path.exists(config["opm_path"]):
            logger.debug("Removing file existing at path")
            os.remove(config["opm_path"])

        logger.debug("Symlinking")
        os.symlink(config["opm_path"] + "-" + version, config["opm_path"])


def load_settings() -> OperatorIndexSettings:
    logger.info("Importing operator-index.yml")
    settings = OperatorIndexSettings.load(file=os.path.join(
        os.path.dirname(__file__),
        "operator-index.yml"
    ))
    logger.debug("Loaded settings: {}".format(settings))
    return settings


# We'll be using these repeatedly
def verbose_opt(func):
    return click.option(
        "-v", "--verbose", count=True,
        help="Increase verbosity (specify multiple times for more)."
    )(func)


def tag_extension_opt(func):
    return click.option(
        "-t", "--tag-extension",
        help="Extend the tag of the index image with an identifier."
    )(func)


def opm_version_opt(func):
    return click.option(
        "-o", "--opm-version", default="latest",
        help="The version of OPM to use to build the index."
    )(func)


def testing_opt(func):
    return click.option(
        "--testing", is_flag=True,
        help=("Convert all bundled operators to the 'develop' tag "
              "and build an index tag named 'testing' instead.")
    )(func)


@click.group(invoke_without_command=True)
@verbose_opt
@click.version_option()
def main(verbose):
    """
    Build and push Operator Framework OLM Catalog Index images
    """
    logger = make_logger(verbose)
    logger.debug(sys.argv)
    logger.debug(f"verbose: {verbose}")


@main.command(name="build")
@verbose_opt
@tag_extension_opt
@opm_version_opt
@testing_opt
def do_build(verbose, tag_extension, opm_version, testing):
    """
    Builds the image locally
    """
    logger = make_logger(verbose)

    logger.info("Downloading opm binary version {}".format(opm_version))
    install_opm(opm_version)

    settings = load_settings()
    if testing:
        runtime = OperatorIndexSettings._determine_runtime()
        for bundle in settings.bundles:
            develop = True  # Default to assuming we can use a develop tag
            for line in shell("{} pull {}:develop".format(runtime, bundle.img),
                              fail=False):  # but actually pull the tags
                if "Error" in line:  # and abort if error
                    logger.info(("Unable to use {0}:develop, defaulting to "
                                 "{0}:{1}").format(bundle.img, bundle.tag))
                    develop = False
                    break
            if develop:
                bundle.tag = "develop"
        settings.index.tag = "testing"

    build_cmd = config["opm_path"] + settings.generate_command_line()
    if tag_extension is not None:
        build_cmd += "-{}".format(tag_extension)

    for line in shell(build_cmd):
        print(line)


@main.command()
@verbose_opt
@tag_extension_opt
@click.option("-e", "--extra-tag", multiple=True,
              help="Also apply and push the extra tags supplied.")
@click.option("--build/--no-build", default=True,
              help=("Whether to build the images if they don't exist "
                    "(default: build)."))
@opm_version_opt
@testing_opt
@click.pass_context
def push(ctx, verbose, tag_extension, extra_tag, build, opm_version, testing):
    """
    Pushes the image with the appropriate tags to the registry
    """
    logger = make_logger(verbose)
    runtime = OperatorIndexSettings._determine_runtime()

    settings = load_settings()
    if testing:
        settings.index.tag = "testing"

    # This is the full built tag, including repository, that we are trying to
    #   push. It may not have been built yet.
    push_tag = "{}:{}".format(
        settings.index.img,
        settings.index.tag
    )
    logger.info("Pushing tag: {}".format(push_tag))

    # Start off by saying we haven't found an image with this tag
    found_image = False
    # Record a list of all images present in the runtime right now
    images = [line for line in shell(
        runtime + " images --format '{{.Repository}}:{{.Tag}}'"
    )]

    if tag_extension is not None:
        # Look for the tag w/ extension in the existing image list
        for tag in images:
            if tag == push_tag + "-" + tag_extension:
                logger.debug("Found tag extension: -{}".format(tag_extension))
                push_tag += "-{}".format(tag_extension)
                found_image = True  # we found the exact extended tag
                break

    if not found_image:
        # Look for the unextended tag in the existing image list because
        #   either no tag extension was specified, or we didn't find the
        #   extended tag in the list on this runtime
        for tag in images:
            if tag == push_tag:
                logger.debug("Found unextended tag")
                found_image = True  # we found this image, without extension
                break

    if not found_image:
        # If we haven't found the image at all, we should maybe build it
        logger.info("Unable to find image")
        # Build it if we should
        if build:  # the user wants us to build a new version
            logger.debug("Doing build")
            if tag_extension is not None:  # then we will be adding it
                # So we need to keep track of that extension
                push_tag += "-{}".format(tag_extension)
            # This actually performs the build
            # NOTE: if testing we point all packaged bundle tags to 'develop'
            #   and set the index tag to 'testing'
            ctx.invoke(do_build, verbose=verbose,
                       tag_extension=tag_extension,
                       opm_version=opm_version,
                       testing=testing)
        else:  # the user doesn't want to build a new version and it's not here
            raise RuntimeError("Unable to find the appropriate image to push.")
    elif tag_extension and not push_tag.endswith("-{}".format(tag_extension)):
        # If we found it, but the tag doesn't match, retag it."
        logger.info(
            "Retagging {0} as {0}-{1}".format(push_tag, tag_extension)
        )
        shell(
            runtime + " tag " + push_tag + " " + push_tag +
            "-{}".format(tag_extension)
        )
        # And make sure that we keep track of what we need to push
        push_tag += "-{}".format(tag_extension)

    # Build out all complete extra tags to push
    extra_tags = [
        "{}:{}".format(settings.index.img, tag) for tag in extra_tag
    ]
    if extra_tags:
        logger.info("Also tagging: {}".format(extra_tags))

    # At this point push_tag is the full tag of an image built here that we
    #   want to push, and extra tags are all the other tags we want to add
    #   directly.
    tag_cmds = [
        runtime + " tag " + push_tag + " " + tag for tag in extra_tags
    ]
    # This adds our extra tags
    for tag_cmd in tag_cmds:
        for line in shell(tag_cmd):
            print(line)

    push_cmds = [runtime + " push " + tag for tag in extra_tags + [push_tag]]
    # This pushes our main tag (maybe with extensions) and our extra tags
    for push_cmd in push_cmds:
        for line in shell(push_cmd):
            print(line)


if __name__ == "__main__":
    main()
