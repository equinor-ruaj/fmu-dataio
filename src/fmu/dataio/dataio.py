"""Module for DataIO class.

The metadata spec is documented as a JSON schema, stored under schema/.
"""

from __future__ import annotations

import os
import uuid
import warnings
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, List, Literal, Optional, Union
from warnings import warn

import pandas as pd
from pydantic import ValidationError as PydanticValidationError

from . import _metadata, types
from ._definitions import (
    FmuContext,
    ValidationError,
)
from ._logging import null_logger
from ._utils import (
    create_symlink,
    detect_inside_rms,  # dataio_examples,
    drop_nones,
    export_file_compute_checksum_md5,
    export_metadata_file,
    filter_validate_metadata,
    generate_description,
    prettyprint_dict,
    read_metadata_from_file,
    some_config_from_env,
    uuid_from_string,
)
from .datastructure._internal.internal import (
    AllowedContent,
    CaseMetadata,
    CaseSchema,
    FMUModel,
)
from .datastructure.configuration import global_configuration
from .datastructure.meta.meta import Access, Masterdata, User

# DATAIO_EXAMPLES: Final = dataio_examples()
INSIDE_RMS: Final = detect_inside_rms()


GLOBAL_ENVNAME: Final = "FMU_GLOBAL_CONFIG"
SETTINGS_ENVNAME: Final = "FMU_DATAIO_CONFIG"  # input settings from a spesific file!

logger: Final = null_logger(__name__)


# ======================================================================================
# Private functions
# ======================================================================================


def _validate_variable(key: str, value: type, legals: dict[str, str | type]) -> bool:
    """Use data from __annotions__ to validate that overriden var. is of legal type."""
    if key not in legals:
        logger.warning("Unsupported key, raise an error")
        raise ValidationError(f"The input key '{key}' is not supported")

    legal_key = legals[key]
    # Potential issue: Eval will use the modules namespace. If given
    #   "from typing import ClassVar" or similar.
    # is missing from the namespace, eval(...) will fail.
    valid_type = eval(legal_key) if isinstance(legal_key, str) else legal_key

    try:
        validcheck = valid_type.__args__
    except AttributeError:
        validcheck = valid_type

    if "typing." not in str(validcheck):
        if not isinstance(value, validcheck):
            logger.warning("Wrong type of value, raise an error")
            raise ValidationError(
                f"The value of '{key}' is of wrong type: {type(value)}. "
                f"Allowed types are {validcheck}"
            )
    else:
        logger.info("Skip type checking of complex types; '%s: %s'", key, validcheck)

    return True


# the two next content key related function may require refactoring/simplification
def _check_content(proposed: str | dict | None) -> Any:
    """Check content and return a validated version."""
    logger.info("Evaluate content")

    content = proposed
    content_specific = None
    logger.debug("content is %s of type %s", str(content), type(content))
    if content is None:
        usecontent = "unset"  # user warnings on this will in _objectdata_provider

    elif isinstance(content, str):
        logger.debug("content is a string")
        if AllowedContent.requires_additional_input(content):
            raise ValidationError(f"content {content} requires additional input")
        usecontent = content
        content_specific = None  # not relevant when content is a string
        logger.debug("usecontent is %s", usecontent)

    elif isinstance(content, dict):
        logger.debug("content is a dictionary")
        usecontent = (list(content.keys()))[0]
        logger.debug("usecontent is %s", usecontent)
        content_specific = content[usecontent]
        logger.debug("content_specific is %s", content_specific)
        if not isinstance(content_specific, dict):
            raise ValueError(
                "Content is incorrectly formatted. When giving content as a dict, "
                "it must be formatted as:"
                "{'mycontent': {extra_key: extra_value} where mycontent is a string "
                "and in the list of valid contents, and extra keys in associated "
                " dictionary must be valid keys for this content."
            )
    else:
        raise ValidationError("The 'content' must be string or dict")

    if usecontent != "unset" and usecontent not in AllowedContent.model_fields:
        raise ValidationError(
            f"Invalid content: <{usecontent}>! "
            f"Valid content: {', '.join(AllowedContent.model_fields.keys())}"
        )

    logger.debug("outgoing content is set to %s", usecontent)
    if content_specific:
        content_specific = _content_validate(usecontent, content_specific)
    else:
        logger.debug("content has no extra information")

    return usecontent, content_specific


def _content_validate(name: str, fields: dict[str, object] | None) -> dict | None:
    try:
        return AllowedContent.model_validate({name: fields}).model_dump(
            exclude_none=True,
            mode="json",
        )[name]
    except PydanticValidationError as e:
        raise ValidationError(
            f"""The field {name} has one or more errors that makes it
impossible to create valid content. The data will still be exported but no
metadata will be made. You are strongly encouraged to correct your
configuration. Invalid configuration may be disallowed in future versions.

Detailed information:
{str(e)}
"""
        )


# ======================================================================================
# Public function to read/load assosiated metadata given a file (e.g. a map file)
# ======================================================================================


def read_metadata(filename: str | Path) -> dict:
    """Read the metadata as a dictionary given a filename.

    If the filename is e.g. /some/path/mymap.gri, the assosiated metafile
    will be /some/path/.mymap.gri.yml (or json?)

    Args:
        filename: The full path filename to the data-object.

    Returns:
        A dictionary with metadata read from the assiated metadata file.
    """
    return read_metadata_from_file(filename)


# ======================================================================================
# ExportData, public class
# ======================================================================================


@dataclass
class ExportData:
    """Class for exporting data with rich metadata in FMU.

    This class sets up the general metadata content to be applied in export. The idea is
    that one ExportData instance can be re-used for several similar export() jobs. For
    example::

        edata = dataio.ExportData(
            config=CFG, content="depth", unit="m", vertical_domain={"depth": "msl"},
            timedata=None, is_prediction=True, is_observation=False,
            tagname="faultlines", workflow="rms structural model",
        )

        for name in ["TopOne", TopTwo", "TopThree"]:
            poly = xtgeo.polygons_from_roxar(PRJ, hname, POL_FOLDER)

            out = ed.export(poly, name=name)

    Almost all keyword settings like ``name``, ``tagname`` etc can be set in both the
    ExportData instance and directly in the ``generate_metadata`` or ``export()``
    function, to provide flexibility for different use cases. If both are set, the
    ``export()`` setting will win followed by ``generate_metadata() and finally
    ExportData()``.

    A note on 'pwd' and 'rootpath' and 'casepath': The 'pwd' is the process working
    directory, which is folder where the process (script) starts. The 'rootpath' is the
    folder from which relative file names are relative to and is normally auto-detected.
    The user can however force set the 'actual' rootpath by providing the input
    `casepath`. In case of running a RMS project interactive on disk::

        /project/foo/resmod/ff/2022.1.0/rms/model                   << pwd
        /project/foo/resmod/ff/2022.1.0/                            << rootpath

        A file:

        /project/foo/resmod/ff/2022.1.0/share/results/maps/xx.gri   << example absolute
                                        share/results/maps/xx.gri   << example relative

    When running an ERT forward job using a normal ERT job (e.g. a script)::

        /scratch/nn/case/realization-44/iter-2                      << pwd
        /scratch/nn/case                                            << rootpath

        A file:

        /scratch/nn/case/realization-44/iter-2/share/results/maps/xx.gri  << absolute
                         realization-44/iter-2/share/results/maps/xx.gri  << relative

    When running an ERT forward job but here executed from RMS::

        /scratch/nn/case/realization-44/iter-2/rms/model            << pwd
        /scratch/nn/case                                            << rootpath

        A file:

        /scratch/nn/case/realization-44/iter-2/share/results/maps/xx.gri  << absolute
                         realization-44/iter-2/share/results/maps/xx.gri  << relative


    Args:
        access_ssdl: Optional. A dictionary that will overwrite or append
            to the default ssdl settings read from the config. Example:
            ``{"access_level": "restricted", "rep_include": False}``

        casepath: To override the automatic and actual ``rootpath``. Absolute path to
            the case root. If not provided, the rootpath will be attempted parsed from
            the file structure or by other means. See also fmu_context, where "case"
            may need an explicit casepath!

        config: Required in order to produce valid metadata, either as key (here) or
            through an environment variable. A dictionary with static settings.
            In the standard case this is read from FMU global variables
            (via fmuconfig). The dictionary must contain some
            predefined main level keys to work with fmu-dataio. If the key is missing or
            key value is None, then it will look for the environment variable
            FMU_GLOBAL_CONFIG to detect the file. If no success in finding the file, a
            UserWarning is made. If both a valid config is provided and
            FMU_GLOBAL_CONFIG is provided in addition, the latter will be used.
            Note that this key shall be set while initializing the instance, ie. it
            cannot be used in ``generate_metadata()`` or ``export()``.
            Note also: If missing or empty, export() may still be done, but without a
            metadata file (this feature may change in future releases).

        content: Optional, default is "depth". Is a string or a dictionary with one key.
            Example is "depth" or {"fluid_contact": {"xxx": "yyy", "zzz": "uuu"}}.
            Content is checked agains a white-list for validation!

        fmu_context: In normal forward models, the fmu_context is ``realization`` which
            is default and will put data per realization. Other contexts may be ``case``
            which will put data relative to the case root (see also casepath). Another
            important context is "preprocessed" which will output to a dedicated
            "preprocessed" folder instead, and metadata will be partially re-used in
            an ERT model run. If a non-FMU run is detected (e.g. you run from project),
            fmu-dataio will detect that and set actual context to None as fall-back
            (unless preprocessed is specified). If value is "preprocessed", see also
            ``reuse_metadata`` key.

        description: A multiline description of the data either as a string or a list
            of strings.

        display_name: Optional, set name for clients to use when visualizing.

        forcefolder: This setting shall only be used as exception, and will make it
            possible to output to a non-standard folder. A ``/`` in front will indicate
            an absolute path*; otherwise it will be relative to casepath or rootpath, as
            dependent on the both fmu_context and the is_observations boolean value. A
            typical use-case is forcefolder="seismic" which will replace the "cubes"
            standard folder for Cube output with "seismics". Use with care and avoid if
            possible! (*) For absolute paths, the class variable
            allow_forcefolder_absolute must set to True.

        grid_model: Currently allowed but planned for deprecation

        include_index: This applies to Pandas (table) data only, and if True then the
            index column will be exported. Deprecated, use class variable
            ``table_include_index`` instead

        is_prediction: True (default) if model prediction data

        is_observation: Default is False. If True, then disk storage will be on the
            "share/observations" folder, otherwise on share/result. An exception arise
            if fmu_context is "preprocessed", then the folder will be set to
            "share/processed" irrespective the value of is_observation.

        name: Optional but recommended. The name of the object. If not set it is tried
            to be inferred from the xtgeo/pandas/... object. The name is then checked
            towards the stratigraphy list, and name is replaced with official
            stratigraphic name if found in static metadata `stratigraphy`. For example,
            if "TopValysar" is the model name and the actual name is "Valysar Top Fm."
            that latter name will be used.

        parent: Optional. This key is required for datatype GridProperty, and
            refers to the name of the grid geometry.

        realization: Optional, default is -999 which means that realization shall be
            detected automatically from the FMU run. Can be used to override in rare
            cases. If so, numbers must be >= 0

        reuse_metadata_rule: This input is None or a string describing rule for reusing
            metadata. Default is None, but if the input is a file string or object with
            already valid metadata, then it is assumed to be "preprocessed", which
            merges the metadata after predefined rules.

        runpath: TODO! Optional and deprecated. The relative location of the current run
            root. Optional and will in most cases be auto-detected, assuming that FMU
            folder conventions are followed. For an ERT run e.g.
            /scratch/xx/nn/case/realization-0/iter-0/. while in a revision at project
            disc it will the revision root e.g. /project/xx/resmod/ff/21.1.0/.

        subfolder: It is possible to set one level of subfolders for file output.
            The input should only accept a single folder name, i.e. no paths. If paths
            are present, a deprecation warning will be raised.

        tagname: This is a short tag description which be be a part of file name.

        timedata: If given, a list of lists with dates, .e.g.
            [[20200101, "monitor"], [20180101, "base"]] or just [[2021010]]. The output
            to metadata will from version 0.9 be different (API change)

        vertical_domain: This is dictionary with a key and a reference e.g.
            {"depth": "msl"} which is default if missing.

        workflow: Short tag desciption of workflow (as description)

        undef_is_zero: Flags that nans should be considered as zero in aggregations


    .. note:: Comment on time formats

        If two dates are present (i.e. the element represents a difference, the input
        time format is on the form::

            timedata: [[20200101, "monitor"], [20180101, "base"]]

        Hence the last data (monitor) usually comes first.

        In the new version this will shown in metadata files as where the oldest date is
        shown as t0::

            data:
              t0:
                value: 2018010T00:00:00
                description: base
              t1:
                value: 202020101T00:00:00
                description: monitor

        The output files will be on the form: somename--t1_t0.ext

    .. note:: Using config from file

        Optionally, the keys can be stored in a yaml file as argument, and you can let
        the environment variable FMU_DATAIO_CONFIG point to that file. This can e.g.
        make it possible for ERT jobs to point to external input config's. For example::

            export FMU_DATAIO_CONFIG="/path/to/mysettings.yml"
            export FMU_GLOBAL_CONFIG="/path/to/global_variables.yml"

        In python:

            eda = ExportData()
            eda.export(obj)


    """

    # ----------------------------------------------------------------------------------
    # This role for this class is to be:
    # - public (end user) interface
    # - collect the full settings from global config, user keys and class variables
    # - process and validate these settings
    # - establish PWD and rootpath
    #
    # Then other classes will further do the detailed metadata processing, cf _MetaData
    # and subsequent classes called by _MetaData
    # ----------------------------------------------------------------------------------

    # class variables
    allow_forcefolder_absolute: ClassVar[bool] = False
    arrow_fformat: ClassVar[str] = "arrow"
    case_folder: ClassVar[str] = "share/metadata"
    createfolder: ClassVar[bool] = True
    cube_fformat: ClassVar[str] = "segy"
    filename_timedata_reverse: ClassVar[bool] = False  # reverse order output file name
    grid_fformat: ClassVar[str] = "roff"
    include_ertjobs: ClassVar[bool] = False  # if True, include jobs.json from ERT
    legacy_time_format: ClassVar[bool] = False
    meta_format: ClassVar[Literal["yaml", "json"]] = "yaml"
    polygons_fformat: ClassVar[str] = "csv"  # or use "csv|xtgeo"
    points_fformat: ClassVar[str] = "csv"  # or use "csv|xtgeo"
    surface_fformat: ClassVar[str] = "irap_binary"
    table_fformat: ClassVar[str] = "csv"
    dict_fformat: ClassVar[str] = "json"
    table_include_index: ClassVar[bool] = False
    verifyfolder: ClassVar[bool] = True
    _inside_rms: ClassVar[bool] = False  # developer only! if True pretend inside RMS

    # input keys (alphabetic)
    access_ssdl: dict = field(default_factory=dict)
    aggregation: bool = False
    casepath: Optional[Union[str, Path]] = None
    config: dict = field(default_factory=dict)
    content: Optional[Union[dict, str]] = None
    depth_reference: str = "msl"
    description: Union[str, list] = ""
    display_name: Optional[str] = None
    fmu_context: Union[FmuContext, str] = (
        FmuContext.REALIZATION
    )  # post init converts to FmuContext
    forcefolder: str = ""
    grid_model: Optional[str] = None
    is_observation: bool = False
    is_prediction: bool = True
    name: str = ""
    undef_is_zero: bool = False
    parent: str = ""
    realization: int = -999
    reuse_metadata_rule: Optional[str] = None
    runpath: Optional[Union[str, Path]] = None
    subfolder: str = ""
    tagname: str = ""
    timedata: Optional[List[list]] = None
    unit: str = ""
    verbosity: str = "DEPRECATED"  # remove in version 2
    vertical_domain: dict = field(default_factory=dict)
    workflow: str = ""
    table_index: Optional[list] = None

    # some keys that are modified version of input, prepended with _use
    _usecontent: dict | str = field(default_factory=dict, init=False)
    _usefmtflag: str = field(default="", init=False)

    # storing resulting state variables for instance, non-public:
    _metadata: dict = field(default_factory=dict, init=False)
    _pwd: Path = field(default_factory=Path, init=False)
    _config_is_valid: bool = field(default=True, init=False)

    # << NB! storing ACTUAL casepath:
    _rootpath: Path = field(default_factory=Path, init=False)

    def __post_init__(self) -> None:
        if self.verbosity != "DEPRECATED":
            warn(
                "Using the 'verbosity' key is now deprecated and will have no "
                "effect and will be removed in near future. Please remove it from the "
                "argument list. Set logging level from client script in the standard "
                "manner instead.",
                UserWarning,
            )
        logger.info("Running __post_init__ ExportData")
        logger.debug("Global config is %s", prettyprint_dict(self.config))

        self.fmu_context = FmuContext.get(self.fmu_context)

        # set defaults for mutable keys
        self.vertical_domain = {"depth": "msl"}

        # if input is provided as an ENV variable pointing to a YAML file; will override
        if SETTINGS_ENVNAME in os.environ:
            external_input = some_config_from_env(SETTINGS_ENVNAME)

            if external_input:
                # derive legal input from dataclass signature
                annots = getattr(self, "__annotations__", {})
                legals = {
                    key: val for key, val in annots.items() if not key.startswith("_")
                }

                for key, value in external_input.items():
                    if _validate_variable(key, value, legals):
                        setattr(self, key, value)

        # global config which may be given as env variable -> a file; will override
        if GLOBAL_ENVNAME in os.environ:
            self.config = some_config_from_env(GLOBAL_ENVNAME) or {}

        self._validate_content_key()
        self._update_globalconfig_from_settings()

        # check state of global config
        self._config_is_valid = global_configuration.is_valid(self.config)
        if self._config_is_valid:
            # TODO: This needs refinement: _config_is_valid should be removed
            self.config = global_configuration.roundtrip(self.config)

        self._establish_pwd_rootpath()
        self._show_deprecations_or_notimplemented()
        logger.info("FMU context is %s", self.fmu_context)
        logger.info("Ran __post_init__")

    def _show_deprecations_or_notimplemented(self) -> None:
        """Warn on deprecated keys or on stuff not implemented yet."""

        if self.runpath:
            warn(
                "The 'runpath' key has currently no function. It will be evaluated for "
                "removal in fmu-dataio version 2. Use 'casepath' instead!",
                PendingDeprecationWarning,
            )

        if self.grid_model:
            warn(
                "The 'grid_model' key has currently no function. It will be evaluated "
                "for removal in fmu-dataio version 2.",
                PendingDeprecationWarning,
            )

    def _validate_content_key(self) -> None:
        """Validate the given 'content' input."""
        self._usecontent, self._content_specific = _check_content(self.content)

    def _validate_fmucontext_key(self) -> None:
        """Validate the given 'fmu_context' input."""
        if isinstance(self.fmu_context, str):
            self.fmu_context = FmuContext.get(self.fmu_context)

    def _update_fmt_flag(self) -> None:
        # treat special handling of "xtgeo" in format name:
        if self.points_fformat == "csv|xtgeo" or self.polygons_fformat == "csv|xtgeo":
            self._usefmtflag = "xtgeo"
        logger.info("Using flag format: <%s>", self._usefmtflag)

    def _update_check_settings(self, newsettings: dict) -> None:
        """Update instance settings (properties) from other routines."""
        logger.info("Try new settings %s", newsettings)

        # derive legal input from dataclass signature
        annots = getattr(self, "__annotations__", {})
        legals = {key: val for key, val in annots.items() if not key.startswith("_")}
        if "config" in legals:
            del legals["config"]  # config cannot be updated

        if "config" in newsettings:
            raise ValueError("Cannot have 'config' outside instance initialization")

        for setting, value in newsettings.items():
            if _validate_variable(setting, value, legals):
                setattr(self, setting, value)
                logger.info("New setting OK for %s", setting)

        self._show_deprecations_or_notimplemented()
        self._validate_content_key()
        self._validate_fmucontext_key()
        logger.info("Validate FMU context which is now %s", self.fmu_context)

    def _update_globalconfig_from_settings(self) -> None:
        """A few user settings may update/append the global config directly."""
        newglobals = deepcopy(self.config)

        if self.access_ssdl:
            if "ssdl" not in self.config["access"]:
                newglobals["access"]["ssdl"] = {}

            newglobals["access"]["ssdl"] = deepcopy(self.access_ssdl)

            logger.info(
                "Updated global config's access.ssdl value: %s", newglobals["access"]
            )

        self.config = newglobals

    def _establish_pwd_rootpath(self) -> None:
        """Establish state variables pwd and the (initial) rootpath.

        The self._pwd stores the process working directory, i.e. the folder
        from which the process is ran

        The self._rootpath stores the folder from which is the base root for all
        relative output files. This rootpath may be dependent on if this is a FMU run
        or just an interactive run.

        Hence this 'initial' rootpath can be updated later!
        """
        logger.info(
            "Establish pwd and actual casepath, inside RMS flag is %s (actual: %s))",
            ExportData._inside_rms,
            INSIDE_RMS,
        )
        self._pwd = Path().absolute()

        # fmu_context 1: Running RMS, we are in conventionally in rootpath/rms/model
        # fmu_context 2: ERT FORWARD_JOB, at case = rootpath=RUNPATH/../../. level
        # fmu_context 3: ERT WORKFLOW_JOB, running somewhere/anywhere else

        self._rootpath = self._pwd
        if self.casepath and isinstance(self.casepath, (str, Path)):
            self._rootpath = Path(self.casepath).absolute()
            logger.info("The casepath is hard set as %s", self._rootpath)

        else:
            if ExportData._inside_rms or INSIDE_RMS:
                logger.info(
                    "Run from inside RMS: ExportData._inside_rms=%s, INSIDE_RMS=%s",
                    ExportData._inside_rms,
                    INSIDE_RMS,
                )
                self._rootpath = (self._pwd / "../../.").absolute().resolve()
                ExportData._inside_rms = True

        logger.info("pwd:        %s", str(self._pwd))
        logger.info("rootpath:   %s", str(self._rootpath))

    def _check_obj_if_file(self, obj: types.Inferrable) -> types.Inferrable:
        """When obj is file-like, it must be checked + assume preprocessed.

        In addition, if preprocessed, derive the name, tagname, subfolder if present and
        those are not set already.
        """

        if isinstance(obj, (str, Path)):
            if isinstance(obj, str):
                obj = Path(obj)
            if not obj.exists():
                raise ValidationError(f"The file {obj} does not exist.")
            if not self.reuse_metadata_rule:
                self.reuse_metadata_rule = "preprocessed"

            currentmeta = read_metadata(obj)
            if "_preprocessed" not in currentmeta:
                raise ValidationError(
                    "The special entry for preprocessed data <_preprocessed> is"
                    "missing in the metadata. A possible solution is to rerun the"
                    "preprocessed export."
                )

            if not self.name and currentmeta["_preprocessed"].get("name", ""):
                self.name = currentmeta["_preprocessed"]["name"]

            if not self.tagname and currentmeta["_preprocessed"].get("tagname", ""):
                self.tagname = currentmeta["_preprocessed"]["tagname"]

            if not self.subfolder and currentmeta["_preprocessed"].get("subfolder", ""):
                self.subfolder = currentmeta["_preprocessed"]["subfolder"]

        return obj

    # ==================================================================================
    # Public methods:
    # ==================================================================================

    def generate_metadata(
        self,
        obj: types.Inferrable,
        compute_md5: bool = True,
        **kwargs: object,
    ) -> dict:
        """Generate and return the complete metadata for a provided object.

        An object may be a map, 3D grid, cube, table, etc which is of a known and
        supported type.

        Examples of such known types are XTGeo objects (e.g. a RegularSurface),
        a Pandas Dataframe, a PyArrow table, etc.

        If the key ``reuse_metadata_rule`` is applied with legal value, the object may
        also be a reference to a file with existing metadata which then will be re-used.

        Args:
            obj: XTGeo instance, a Pandas Dataframe instance or other supported object.
            compute_md5: If True, compute a MD5 checksum for the exported file.
            **kwargs: For other arguments, see ExportData() input keys. If they
                exist both places, this function will override!

        Returns:
            A dictionary with all metadata.

        Note:
            If the ``compute_md5`` key is False, the ``file.checksum_md5`` will be
            empty. If true, the MD5 checksum will be generated based on export to
            a temporary file, which may be time-consuming if the file is large.
        """
        logger.info("Generate metadata...")
        logger.info("KW args %s", kwargs)

        self._update_check_settings(kwargs)
        self._update_globalconfig_from_settings()

        self._config_is_valid = global_configuration.is_valid(self.config)
        if self._config_is_valid:
            # TODO: This needs refinement: _config_is_valid should be removed
            self.config = global_configuration.roundtrip(self.config)

        obj = self._check_obj_if_file(obj)
        self._establish_pwd_rootpath()
        self._validate_content_key()
        self._update_fmt_flag()

        metaobj = _metadata.MetaData(obj, self, compute_md5=compute_md5)
        self._metadata = metaobj.generate_export_metadata()

        self._rootpath = Path(metaobj.rootpath)

        logger.info("The metadata are now ready!")

        return deepcopy(self._metadata)

    def export(
        self,
        obj: types.Inferrable,
        return_symlink: bool = False,
        **kwargs: Any,
    ) -> str:
        """Export data objects of 'known' type to FMU storage solution with metadata.

        This function will also collect the data spesific class metadata. For "classic"
        files, the metadata will be stored i a YAML file with same name stem as the
        data, but with a . in front and "yml" and suffix, e.g.::

            top_volantis--depth.gri
            .top_volantis--depth.gri.yml

        Args:
            obj: XTGeo instance, a Pandas Dataframe instance or other supported object.
            return_symlink: If fmu_context is 'case_symlink_realization' then the link
                adress will be returned if this is True; otherwise the physical file
                path will be returned.
            **kwargs: For other arguments, see ExportData() input keys. If they
                exist both places, this function will override!

        Returns:
            String: full path to exported item.
        """
        self.table_index = kwargs.get("table_index", self.table_index)
        self.generate_metadata(obj, compute_md5=False, **kwargs)
        metadata = self._metadata

        outfile = Path(metadata["file"]["absolute_path"])
        metafile = outfile.parent / ("." + str(outfile.name) + ".yml")

        useflag = (
            self.table_include_index
            if isinstance(obj, pd.DataFrame)
            else self._usefmtflag
        )

        obj = self._check_obj_if_file(obj)
        logger.info("Export to file and compute MD5 sum, using flag: <%s>", useflag)
        # inject md5 checksum in metadata
        metadata["file"]["checksum_md5"] = export_file_compute_checksum_md5(
            obj,
            outfile,
            flag=useflag,  # type: ignore
            # BUG(?): Looks buggy, if flag is bool export_file will blow up.
        )
        logger.info("Actual file is:   %s", outfile)

        if self._config_is_valid:
            export_metadata_file(metafile, metadata, savefmt=self.meta_format)
            logger.info("Metadata file is: %s", metafile)
        else:
            warnings.warn("Data will be exported, but without metadata.", UserWarning)

        # generate symlink if requested
        outfile_target = None
        if metadata["file"].get("absolute_path_symlink"):
            outfile_target = Path(metadata["file"]["absolute_path_symlink"])
            outfile_source = Path(metadata["file"]["absolute_path"])
            create_symlink(str(outfile_source), str(outfile_target))
            metafile_target = outfile_target.parent / ("." + str(outfile.name) + ".yml")
            create_symlink(str(metafile), str(metafile_target))

        self._metadata = metadata

        if return_symlink and outfile_target:
            return str(outfile_target)
        return str(outfile)


# ######################################################################################
# InitializeCase.
#
# The InitializeCase is used for making the case matadata prior to any other actions,
# e.g. forward jobs. However, case metadata file may already exist, and in that case
# this class should only emit a message or warning.
# ######################################################################################


@dataclass
class InitializeCase:  # pylint: disable=too-few-public-methods
    """Initialize metadata for an FMU Case.

    In ERT this is typically ran as an hook workflow in advance.

    Args:
        config: A configuration dictionary. In the standard case this is read
            from FMU global variables (via fmuconfig). The dictionary must contain
            some predefined main level keys. If config is None or the env variable
            FMU_GLOBAL_CONFIG pointing to a file is provided, then it will attempt to
            parse that file instead.
        rootfolder: Absolute path to the case root, including case name.
        casename: Name of case (experiment)
        caseuser: Username provided
        description (Optional): Description text as string or list of strings.
    """

    # class variables
    meta_format: ClassVar[Literal["yaml", "json"]] = "yaml"

    config: dict
    rootfolder: str | Path
    casename: str
    caseuser: str

    description: Optional[Union[str, list]] = None

    _metadata: dict = field(default_factory=dict, init=False)
    _metafile: Path = field(default_factory=Path, init=False)
    _pwd: Path = field(default_factory=Path, init=False)
    _casepath: Path = field(default_factory=Path, init=False)

    def __post_init__(self) -> None:
        self._pwd = Path().absolute()
        self._casepath = Path(self.rootfolder)
        self._metafile = self._casepath / "share/metadata/fmu_case.yml"

        # For this class, the global config must be valid; hence error if not
        try:
            global_configuration.GlobalConfiguration.model_validate(self.config)
        except PydanticValidationError as e:
            global_configuration.validation_error_warning(e)
            raise
        logger.info("Ran __post_init__ for InitializeCase")

    def _establish_metadata_files(self) -> bool:
        """Checks if the metadata files and directories are established and creates
        relevant directories and files if not.

        Returns:
            False if fmu_case.yml exists (not established), True if it doesn't.
        """
        if not self._metafile.parent.exists():
            self._metafile.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Created rootpath (case) %s", self._casepath)
        logger.info("The requested metafile is %s", self._metafile)
        return not self._metafile.exists()

    def _case_uuid(self) -> uuid.UUID:
        """
        Generates and persists a unique UUID for a new case.

        Upon initialization of a new case, this UUID is stored in case
        metadata and written to disk, ensuring it remains constant for the case across
        runs and exports. It is foundational for tracking cases and embedding
        identifiers into file metadata.
        """
        return uuid.uuid4()

    # ==================================================================================
    # Public methods:
    # ==================================================================================

    def generate_metadata(self) -> dict:
        """Generate case metadata.

        Returns:
            A dictionary with case metadata or an empty dictionary if the metadata
            already exists.
        """
        if not self._establish_metadata_files():
            exists_warning = (
                "The case metadata file already exists and will not be overwritten. "
                "To make new case metadata delete the old case or run on a different "
                "runpath."
            )
            logger.warning(exists_warning)
            warn(exists_warning, UserWarning)
            return {}

        meta = CaseSchema(
            masterdata=Masterdata.model_validate(self.config["masterdata"]),
            access=Access.model_validate(_metadata.generate_meta_access(self.config)),
            fmu=FMUModel(
                model=global_configuration.Model.model_validate(
                    self.config["model"],
                ),
                case=CaseMetadata(
                    name=self.casename,
                    uuid=str(self._case_uuid()),
                    user=User(id=self.caseuser),
                ),
            ),
            tracklog=_metadata.generate_meta_tracklog(),
            description=generate_description(self.description),
        ).model_dump(
            mode="json",
            exclude_none=True,
            by_alias=True,
        )

        self._metadata = drop_nones(meta)

        logger.info("The case metadata are now ready!")
        return deepcopy(self._metadata)

    def export(self) -> str:
        """Export case metadata to file.

        Returns:
            Full path of metadata file.
        """
        if self.generate_metadata():
            export_metadata_file(
                self._metafile, self._metadata, savefmt=self.meta_format
            )
            logger.info("METAFILE %s", self._metafile)
        return str(self._metafile)


# ######################################################################################
# AggregatedData
#
# The AggregatedData is used for making the aggregations from existing data that already
# have valid metadata, i.e. made from ExportData.
#
# Hence this is actually quite different and simpler than ExportData(), which
# needed a lot of info as FmuProvider, FileProvider, ObjectData etc. Here most these
# already known from the input.
#
# For aggregations, the id is normally given as an argument by the external process, and
# by that, be able to give a group of aggregations the same id.
#
# ######################################################################################


@dataclass
class AggregatedData:
    """Instantate AggregatedData object.

    Args:
        aggregation_id: Give an explicit ID for the aggregation. If None, an ID will be
        made based on existing realization uuids.
        casepath: The root folder to the case, default is None. If None, the casepath
            is derived from the first input metadata paths (cf. ``source_metadata``) if
            possible. If given explicitly, the physical casepath folder must exist in
            advance, otherwise a ValueError will be raised.
        source_metadata: A list of individual metadata dictionarys, coming from the
            valid metadata per input element that forms the aggregation.
        operation: A string that describes the operation, e.g. "mean". This is
            mandatory and there is no default.
        tagname: Additional name, as part of file name
    """

    # class variable(s)
    meta_format: ClassVar[Literal["yaml", "json"]] = "yaml"

    # instance
    aggregation_id: Optional[str] = None
    casepath: Optional[Union[str, Path]] = None
    source_metadata: list = field(default_factory=list)
    name: str = ""
    operation: str = ""
    tagname: str = ""
    verbosity: str = "DEPRECATED"  # keep for while

    _metadata: dict = field(default_factory=dict, init=False)
    _metafile: Path = field(default_factory=Path, init=False)

    def __post_init__(self) -> None:
        if self.verbosity != "DEPRECATED":
            warn(
                "Using the 'verbosity' key is now deprecated and will have no "
                "effect and will be removed in near future. Please remove it from the "
                "argument list. Set logging level from client script in the standard "
                "manner instead.",
                UserWarning,
            )

    @staticmethod
    def _generate_aggr_uuid(uuids: list[str]) -> str:
        """Unless aggregation_id; use existing UUIDs to generate a new UUID."""
        return uuid_from_string("".join(sorted(uuids)))

    def _update_settings(self, newsettings: dict) -> None:
        """Update instance settings (properties) from other routines."""
        logger.info("Try new settings %s", newsettings)

        # derive legal input from dataclass signature
        annots = getattr(self, "__annotations__", {})
        legals = {key: val for key, val in annots.items() if not key.startswith("_")}

        for setting, value in newsettings.items():
            if _validate_variable(setting, value, legals):
                setattr(self, setting, value)
                logger.info("New setting OK for %s", setting)

    def _construct_filename(self, template: dict) -> tuple[Path, Path | None]:
        """Construct the paths/filenames for aggregated data.

        These filenames are constructed a bit different than in a forward job, since we
        do not now which folder we 'are in' when doing aggregations. Could possibly also
        be in a cloud setting.

        Hence we use the first input realization as template, e.g.:

        file:
           relative_path: realization-33/iter-0/share/results/maps/x.gri
           absolute_path: /scratch/f/case/realization-33/iter-0/share/results/maps/x.gri

        And from thet we derive/compose the relative and absolute path for the
        aggregated data:

        file:
           relative_path: iter-0/share/results/maps/aggr.gri
           absolute_path: /scratch/f/case/iter-0/share/results/maps/aggr.gri

        The trick is to replace 'realization-*' with nothing and create a new file
        name.

        -----
        However, there are also the scenario that absolute_path are missing (e.g. all
        input realizations are directly made in cloud setting), and we need to
        account for that:

        infile:
           relative_path: realization-33/iter-0/share/results/maps/x.gri
           absolute_path: none

        file:
           relative_path: iter-0/share/results/maps/aggr.gri
           absolute_path: none

        -----
        Finally, a user given casepath (casepath is not None) should replace the current
        root part in the files. Like this:

        infile:
           relative_path: realization-33/iter-0/share/results/maps/x.gri
           absolute_path: /scratch/f/case/realization-33/iter-0/share/results/maps/x.gri

        casepath = /scratch/f/othercase

        result:
           relative_path: iter-0/share/results/maps/aggr.gri
           absolute_path: /scratch/f/othercase/iter-0/share/results/maps/aggrd.gri

        """
        logger.info("Construct file name for the aggregation...")
        realiname = template["fmu"]["realization"]["name"]
        relpath = template["file"]["relative_path"]

        if template["file"].get("absolute_path", None):
            abspath = template["file"]["absolute_path"]
        else:
            abspath = None

        logger.info("First input realization relpath is: %s ", relpath)
        logger.info("First input realization abspath is: %s ", abspath)

        if self.casepath:
            casepath = Path(self.casepath)
            if not casepath.exists():
                raise ValueError(
                    f"The given casepath {casepath} does not exist. "
                    "It must exist in advance!"
                )
            abspath = str(casepath / relpath)

        relpath = relpath.replace(realiname + "/", "")
        relpath = Path(relpath)
        if abspath:
            abspath = abspath.replace(realiname + "/", "")
            abspath = Path(abspath)

        suffix = relpath.suffix
        stem = relpath.stem

        usename = stem + "--" + self.operation
        if not self.name:
            warn("Input name is not given, will assume <usename>", UserWarning)
        else:
            usename = self.name

        if self.tagname:
            usename = usename + "--" + self.tagname

        relname = (relpath.parent / usename).with_suffix(suffix)

        absname = None
        if abspath:
            absname = (abspath.parent / usename).with_suffix(suffix)

        logger.info("New relpath is: %s ", relname)
        logger.info("New abspath is: %s ", absname)

        return relname, absname

    def _generate_aggrd_metadata(
        self,
        obj: types.Inferrable,
        real_ids: list[int],
        uuids: list[str],
        compute_md5: bool = True,
    ) -> None:
        logger.info(
            "self.aggregation is %s (%s)",
            self.aggregation_id,
            type(self.aggregation_id),
        )

        if self.aggregation_id is None:
            self.aggregation_id = self._generate_aggr_uuid(uuids)
        else:
            if not isinstance(self.aggregation_id, str):
                raise ValueError("aggregation_id must be a string")

        if not self.operation:
            raise ValueError("The 'operation' key has no value")

        # use first as template but filter away invalid entries first:
        template = filter_validate_metadata(self.source_metadata[0])

        relpath, abspath = self._construct_filename(template)

        # fmu.realization shall not be used
        del template["fmu"]["realization"]

        template["fmu"]["aggregation"] = {}
        template["fmu"]["aggregation"]["operation"] = self.operation
        template["fmu"]["aggregation"]["realization_ids"] = real_ids
        template["fmu"]["aggregation"]["id"] = self.aggregation_id

        # fmu.context.stage should be 'iteration'
        template["fmu"]["context"]["stage"] = "iteration"

        # next, the new object will trigger update of: 'file', 'data' (some fields) and
        # 'tracklog'. The trick is to create an ExportData() instance and just retrieve
        # the metadata from that, and then blend the needed metadata from here into the
        # template -> final metadata

        fakeconfig = {
            "access": self.source_metadata[0]["access"],
            "masterdata": self.source_metadata[0]["masterdata"],
            "model": self.source_metadata[0]["fmu"]["model"],
        }

        content = template["data"]["content"]
        etemp = ExportData(config=fakeconfig, name=self.name, content=content)
        etempmeta = etemp.generate_metadata(obj, compute_md5=compute_md5)

        template["tracklog"] = etempmeta["tracklog"]
        template["file"] = etempmeta["file"]  # actually only use the checksum_md5
        template["file"]["relative_path"] = str(relpath)
        template["file"]["absolute_path"] = str(abspath) if abspath else None

        # data section
        if self.name:
            template["data"]["name"] = self.name
        if self.tagname:
            template["data"]["tagname"] = self.tagname
        if etempmeta["data"].get("bbox"):
            template["data"]["bbox"] = etempmeta["data"]["bbox"]

        self._metadata = template

    # ==================================================================================
    # Public methods:
    # ==================================================================================

    def generate_metadata(
        self,
        obj: types.Inferrable,
        compute_md5: bool = True,
        skip_null: bool = True,
        **kwargs: object,
    ) -> dict:
        """Generate metadata for the aggregated data.

        This is a quite different and much simpler operation than the ExportData()
        version, as here most metadata for each input element are already known. Hence,
        the metadata for the first element in the input list is used as template.

        Args:

            obj: The map, 3D grid, table, etc instance.

            compute_md5: If True, an md5 sum for the file will be created. This involves
                a temporary export of the data, and may be time consuming for large
                data.

            skip_null: If True (default), None values in putput will be skipped
            **kwargs: See AggregatedData() arguments; initial will be overridden by
                settings here.
        """
        logger.info("Generate metadata for class")
        self._update_settings(kwargs)

        # get input realization numbers:
        real_ids = []
        uuids = []
        for conf in self.source_metadata:
            try:
                rid = conf["fmu"]["realization"]["id"]
                xuuid = conf["fmu"]["realization"]["uuid"]
            except Exception as error:
                raise ValidationError(f"Seems that input config are not valid: {error}")

            real_ids.append(rid)
            uuids.append(xuuid)

        # first config file as template
        self._generate_aggrd_metadata(obj, real_ids, uuids, compute_md5)
        if skip_null:
            self._metadata = drop_nones(self._metadata)

        return deepcopy(self._metadata)

    # alias method
    def generate_aggregation_metadata(
        self,
        obj: types.Inferrable,
        compute_md5: bool = True,
        skip_null: bool = True,
        **kwargs: object,
    ) -> dict:
        """Alias method name, see ``generate_metadata``"""
        return self.generate_metadata(
            obj, compute_md5=compute_md5, skip_null=skip_null, **kwargs
        )

    def export(self, obj: types.Inferrable, **kwargs: object) -> str:
        """Export aggregated file with metadata to file.

        Args:
            obj: Aggregated object to export, e.g. a XTGeo RegularSurface
            **kwargs: See AggregatedData() arguments; initial will be overridden by
                settings here.
        Returns:
            String: full path to exported item.
        """
        self._update_settings(kwargs)

        metadata = self.generate_metadata(obj, compute_md5=False)

        abspath = metadata["file"].get("absolute_path", None)

        if not abspath:
            raise OSError(
                "The absolute_path is None, hence no export is possible. "
                "Use the ``casepath`` key to provide a valid absolute path."
            )

        outfile = Path(abspath)
        outfile.parent.mkdir(parents=True, exist_ok=True)
        metafile = outfile.parent / ("." + str(outfile.name) + ".yml")

        logger.info("Export to file and compute MD5 sum")
        # inject the computed md5 checksum in metadata
        metadata["file"]["checksum_md5"] = export_file_compute_checksum_md5(
            obj, outfile
        )

        export_metadata_file(metafile, metadata, savefmt=self.meta_format)
        logger.info("Actual file is:   %s", outfile)
        logger.info("Metadata file is: %s", metafile)

        self._metadata = metadata
        return str(outfile)
