from __future__ import annotations

from collections import ChainMap
from pathlib import Path
from typing import Dict, List, Literal, Optional, TypeVar, Union
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    GetJsonSchemaHandler,
    NaiveDatetime,
    RootModel,
    model_validator,
)
from pydantic_core import CoreSchema
from typing_extensions import Annotated

from . import content, enums

T = TypeVar("T", Dict, List, object)


class Asset(BaseModel):
    """The ``access.asset`` block contains information about the owner asset of
    these data."""

    name: str = Field(examples=["Drogon"])
    """A string referring to a known asset name."""


class Ssdl(BaseModel):
    """
    The ``access.ssdl`` block contains information related to SSDL.
    Note that this is kept due to legacy.
    """

    access_level: enums.Classification
    """The SSDL access level. See :class:`enums.Classification`."""

    rep_include: bool
    """Flag if this data is to be shown in REP or not."""


class Access(BaseModel):
    """
    The ``access`` block contains information related to access control for
    this data object.
    """

    asset: Asset
    """A block containing information about the owner asset of these data.
    See :class:`Asset`."""

    classification: Optional[enums.Classification] = Field(default=None)
    """The access classification level. See :class:`enums.Classification`."""


class SsdlAccess(Access):
    """
    The ``access`` block contains information related to access control for
    this data object, with legacy SSDL settings.
    """

    ssdl: Ssdl
    """A block containing information related to SSDL. See :class:`Ssdl`."""


class File(BaseModel):
    """
    The ``file`` block contains references to this data object as a file on a disk.
    A filename in this context can be actual, or abstract. Particularly the
    ``relative_path`` is, and will most likely remain, an important identifier for
    individual file objects within an FMU case - irrespective of the existance of an
    actual file system. For this reason, the ``relative_path`` - as well as the
    ``checksum_md5`` will be generated even if a file is not saved to disk. The
    ``absolute_path`` will only be generated in the case of actually creating a file on
    disk and is not required under this schema.
    """

    absolute_path: Optional[Path] = Field(
        default=None,
        examples=["/abs/path/share/results/maps/volantis_gp_base--depth.gri"],
    )
    """The absolute path of a file, e.g. /scratch/field/user/case/etc."""

    relative_path: Path = Field(
        examples=["share/results/maps/volantis_gp_base--depth.gri"],
    )
    """The path of a file relative to the case root."""

    checksum_md5: Optional[str] = Field(examples=["kjhsdfvsdlfk23knerknvk23"])
    """A valid MD5 checksum of the file."""

    size_bytes: Optional[int] = Field(default=None)
    """Size of file object in bytes"""

    relative_path_symlink: Optional[Path] = Field(default=None)
    """The path to a symlink of the relative path."""

    absolute_path_symlink: Optional[Path] = Field(default=None)
    """The path to a symlink of the absolute path."""

    @model_validator(mode="before")
    @classmethod
    def _check_for_non_ascii_in_path(cls, values: Dict) -> Dict:
        if (path := values.get("absolute_path")) and not str(path).isascii():
            raise ValueError(
                f"Path has non-ascii elements which is not supported: {path}"
            )
        return values


class Parameters(RootModel):
    """
    The ``parameters`` block contains the parameters used in a realization. It is a
    direct pass of ``parameters.txt`` and will contain key:value pairs representing the
    parameters.
    """

    root: Dict[str, Union[Parameters, int, float, str]]
    """A dictionary representing parameters as-is from parameters.txt."""


class Aggregation(BaseModel):
    """
    The ``fmu.aggregation`` block contains information about an aggregation
    performed over an ensemble.
    """

    id: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """The unique identifier of an aggregation."""

    operation: str
    """A string representing the type of aggregation performed."""

    realization_ids: List[int]
    """An array of realization ids included in this aggregation."""

    parameters: Optional[Parameters] = Field(default=None)
    """Parameters for this realization. See :class:`Parameters`."""


class Workflow(BaseModel):
    """
    The ``fmu.workflow`` block refers to specific subworkflows within the large
    FMU workflow being ran. This has not been standardized, mainly due to the lack of
    programmatic access to the workflows being run in important software within FMU.

    .. note:: A key usage of ``fmu.workflow.reference`` is related to ensuring
       uniqueness of data objects.
    """

    reference: str
    """A string referring to which workflow this data object was exported by."""


class User(BaseModel):
    """The ``user`` block holds information about the user."""

    id: str = Field(examples=["peesv", "jriv"])
    """A user identity reference."""


class Case(BaseModel):
    """
    The ``fmu.case`` block contains information about the case from which this data
    object was exported.

    A case represent a set of iterations that belong together, either by being part of
    the same run (i.e. history matching) or by being placed together by the user,
    corresponding to /scratch/<asset>/<user>/<my case name>/.

    .. note:: If an FMU data object is exported outside the case context, this block
       will not be present.
    """

    name: str = Field(examples=["MyCaseName"])
    """The name of the case."""

    user: User
    """A block holding information about the user.
    See :class:`User`."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """The unique identifier of this case. Currently made by fmu.dataio."""

    description: Optional[List[str]] = Field(default=None)
    """A free-text description of this case."""


class Iteration(BaseModel):
    """
    The ``fmu.iteration`` block contains information about the iteration this data
    object belongs to.
    """

    id: Optional[int] = Field(default=None)
    """The internal identification of this iteration, typically represented by an
    integer."""

    name: str = Field(examples=["iter-0"])
    """The name of the iteration. This is typically reflecting the folder name on
    scratch. In ERT, custom names for iterations are supported, e.g. "pred"."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """The unique identifier of this case. Currently made by fmu.dataio."""

    restart_from: Optional[UUID] = Field(
        default=None,
        examples=["15ce3b84-766f-4c93-9050-b154861f9100"],
    )
    """A uuid reference to another iteration that this iteration was restarted
    from"""


class Model(BaseModel):
    """The ``fmu.model`` block contains information about the model used.

    .. note::
       Synonyms for "model" in this context are "template", "setup", etc. The term
       "model" is ultra-generic but was chosen before e.g. "template" as the latter
       deviates from daily communications and is, if possible, even more generic
       than "model".
    """

    description: Optional[List[str]] = Field(default=None)
    """This is a free text description of the model setup"""

    name: str = Field(examples=["Drogon"])
    """The name of the model."""

    revision: str = Field(examples=["21.0.0.dev"])
    """The revision of the model."""


class Realization(BaseModel):
    """
    The ``fmu.realization`` block contains information about the realization this
    data object belongs to.
    """

    id: int
    """The internal ID of the realization, typically represented by an integer."""

    name: str = Field(examples=["iter-0"])
    """The name of the realization. This is typically reflecting the folder name on
    scratch. We recommend to use ``fmu.realization.id`` for all usage except purely
    visual appearance."""

    parameters: Optional[Parameters] = Field(default=None)
    """These are the parameters used in this realization. It is a direct pass of
    ``parameters.txt`` and will contain key:value pairs representing the design
    parameters. See :class:`Parameters`."""

    jobs: Optional[object] = Field(default=None)
    """Content directly taken from the ERT jobs.json file for this realization."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """The universally unique identifier for this realization. It is a hash of
    ``fmu.case.uuid`` and ``fmu.iteration.uuid`` and ``fmu.realization.id``."""


class CountryItem(BaseModel):
    """A single country in the ``smda.masterdata.country`` list of countries
    known to SMDA."""

    identifier: str = Field(examples=["Norway"])
    """Identifier known to SMDA."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """Identifier known to SMDA."""


class DiscoveryItem(BaseModel):
    """A single discovery in the ``masterdata.smda.discovery`` list of discoveries
    known to SMDA."""

    short_identifier: str = Field(examples=["SomeDiscovery"])
    """Identifier known to SMDA."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """Identifier known to SMDA."""


class FieldItem(BaseModel):
    """A single field in the ``masterdata.smda.field`` list of fields
    known to SMDA."""

    identifier: str = Field(examples=["OseFax"])
    """Identifier known to SMDA."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """Identifier known to SMDA."""


class CoordinateSystem(BaseModel):
    """The ``masterdata.smda.coordinate_system`` block contains the coordinate
    system known to SMDA."""

    identifier: str = Field(examples=["ST_WGS84_UTM37N_P32637"])
    """Identifier known to SMDA."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """Identifier known to SMDA."""


class StratigraphicColumn(BaseModel):
    """The ``masterdata.smda.stratigraphic_column`` block contains the
    stratigraphic column known to SMDA."""

    identifier: str = Field(examples=["DROGON_2020"])
    """Identifier known to SMDA."""

    uuid: UUID = Field(examples=["15ce3b84-766f-4c93-9050-b154861f9100"])
    """Identifier known to SMDA."""


class Smda(BaseModel):
    """The ``masterdata.smda`` block contains SMDA-related attributes."""

    coordinate_system: CoordinateSystem
    """Reference to coordinate system known to SMDA.
    See :class:`CoordinateSystem`."""

    country: List[CountryItem]
    """A list referring to countries known to SMDA. First item is primary.
    See :class:`CountryItem`."""

    discovery: List[DiscoveryItem]
    """A list referring to discoveries known to SMDA. First item is primary.
    See :class:`DiscoveryItem`."""

    field: List[FieldItem]
    """A list referring to fields known to SMDA. First item is primary.
    See :class:`FieldItem`."""

    stratigraphic_column: StratigraphicColumn
    """Reference to stratigraphic column known to SMDA.
    See :class:`StratigraphicColumn`."""


class Masterdata(BaseModel):
    """The ``masterdata`` block contains information related to masterdata.
    Currently, SMDA holds the masterdata.
    """

    smda: Smda
    """Block containing SMDA-related attributes. See :class:`Smda`."""


class Version(BaseModel):
    """
    A generic block that contains a string representing the version of
    something.
    """

    version: str
    """A string representing the version."""


class OperatingSystem(BaseModel):
    """
    The ``operating_system`` block contains information about the OS on which the
    ensemble was run.
    """

    hostname: str = Field(examples=["st-123.equinor.com"])
    """A string containing the network name of the machine."""

    operating_system: str = Field(examples=["Darwin-18.7.0-x86_64-i386-64bit"])
    """A string containing the name of the operating system implementation."""

    release: str = Field(examples=["18.7.0"])
    """A string containing the level of the operating system."""

    system: str = Field(examples=["GNU/Linux"])
    """A string containing the name of the operating system kernel."""

    version: str = Field(examples=["#1 SMP Tue Aug 27 21:37:59 PDT 2019"])
    """The specific release version of the system."""


class SystemInformation(BaseModel):
    """
    The ``tracklog.sysinfo`` block contains information about the system upon which
    these data were exported from.
    """

    fmu_dataio: Optional[Version] = Field(
        alias="fmu-dataio",
        default=None,
        examples=["1.2.3"],
    )
    """The version of fmu-dataio used to export the data. See :class:`Version`."""

    komodo: Optional[Version] = Field(
        default=None,
        examples=["2023.12.05-py38"],
    )
    """The version of Komodo in which the the ensemble was run from."""

    operating_system: Optional[OperatingSystem] = Field(default=None)
    """The operating system from which the ensemble was started from.
    See :class:`OperatingSystem`."""


class TracklogEvent(BaseModel):
    """The ``tracklog`` block contains a record of events recorded on these data.
    This data object describes a tracklog event.
    """

    # TODO: Update ex. to inc. timezone
    # update NaiveDatetime ->  AwareDatetime
    # On upload, sumo adds timezone if its lacking.
    # For roundtripping i need an Union here.
    datetime: Union[NaiveDatetime, AwareDatetime] = Field(
        examples=["2020-10-28T14:28:02"],
    )
    """A datetime representation recording when the event occurred."""

    event: str = Field(examples=["created", "updated", "merged"])
    """A string containing a reference to the type of event being logged."""

    user: User
    """The user who caused the event to happen. See :class:`User`."""

    sysinfo: Optional[SystemInformation] = Field(
        default_factory=SystemInformation,
    )
    """Information about the system on which the event occurred.
    See :class:`SystemInformation`."""


class Display(BaseModel):
    """
    The ``display`` block contains information related to how this data object
    should/could be displayed. As a general rule, the consumer of data is responsible
    for figuring out how a specific data object shall be displayed. However, we use
    this block to communicate preferences from the data producers perspective.

    We also maintain this block due to legacy reasons. No data filtering logic should
    be placed on the ``display`` block.
    """

    name: Optional[str] = Field(default=None)
    """A display-friendly version of ``data.name``."""


class Context(BaseModel):
    """
    The ``fmu.context`` block contains the FMU context in which this data object
    was produced.
    """

    stage: enums.FMUContext
    """The stage of an FMU experiment in which this data was produced.
    See :class:`enums.FMUContext`."""


class FMUCaseAttributes(BaseModel):
    """
    The ``fmu`` block contains all attributes specific to FMU. The idea is that the FMU
    results data model can be applied to data from *other* sources - in which the
    fmu-specific stuff may not make sense or be applicable.
    """

    case: Case
    """The ``fmu.case`` block contains information about the case from which this data
    object was exported. See :class:`Case`."""

    model: Model
    """The ``fmu.model`` block contains information about the model used.
    See :class:`Model`."""


class FMUAttributes(FMUCaseAttributes):
    """
    The ``fmu`` block contains all attributes specific to FMU. The idea is that the FMU
    results data model can be applied to data from *other* sources - in which the
    fmu-specific stuff may not make sense or be applicable.
    """

    context: Context
    """The ``fmu.context`` block contains the FMU context in which this data object
    was produced. See :class:`Context`.  """

    iteration: Optional[Iteration] = Field(default=None)
    """The ``fmu.iteration`` block contains information about the iteration this data
    object belongs to. See :class:`Iteration`. """

    workflow: Optional[Workflow] = Field(default=None)
    """The ``fmu.workflow`` block refers to specific subworkflows within the large
    FMU workflow being ran. See :class:`Workflow`."""

    aggregation: Optional[Aggregation] = Field(default=None)
    """The ``fmu.aggregation`` block contains information about an aggregation
    performed over an ensemble. See :class:`Aggregation`."""

    realization: Optional[Realization] = Field(default=None)
    """The ``fmu.realization`` block contains information about the realization this
    data object belongs to. See :class:`Realization`."""

    @model_validator(mode="before")
    @classmethod
    def _dependencies_aggregation_realization(cls, values: Dict) -> Dict:
        aggregation, realization = values.get("aggregation"), values.get("realization")
        if aggregation and realization:
            raise ValueError(
                "Both 'aggregation' and 'realization' cannot be set "
                "at the same time. Please set only one."
            )
        return values

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> Dict[str, object]:
        json_schema = super().__get_pydantic_json_schema__(core_schema, handler)
        json_schema = handler.resolve_ref_schema(json_schema)
        json_schema.update(
            {
                "dependencies": {
                    "aggregation": {"not": {"required": ["realization"]}},
                    "realization": {"not": {"required": ["aggregation"]}},
                }
            }
        )
        return json_schema


class MetadataBase(BaseModel):
    """Base model for all root metadata models generated."""

    class_: enums.FMUClass = Field(
        alias="class",
        title="metadata_class",
    )

    masterdata: Masterdata
    """The ``masterdata`` block contains information related to masterdata.
    See :class:`Masterdata`."""

    tracklog: List[TracklogEvent]
    """The ``tracklog`` block contains a record of events recorded on these data.
    See :class:`TracklogEvent`."""

    source: Literal["fmu"]
    """The source of this data. Defaults to 'fmu'."""

    version: Literal["0.8.0"]
    """The version of the schema that generated this data."""


class CaseMetadata(MetadataBase):
    """The FMU metadata model for an FMU case.

    A case represent a set of iterations that belong together, either by being part of
    the same run (i.e. history matching) or by being placed together by the user,
    corresponding to /scratch/<asset>/<user>/<my case name>/.
    """

    class_: Literal[enums.FMUClass.case] = Field(
        alias="class",
        title="metadata_class",
    )
    """The class of this metadata object. In this case, always an FMU case."""

    fmu: FMUCaseAttributes
    """The ``fmu`` block contains all attributes specific to FMU.
    See :class:`FMUCaseAttributes`."""

    access: Access
    """The ``access`` block contains information related to access control for
    this data object. See :class:`Access`."""


class ObjectMetadata(MetadataBase):
    """The FMU metadata model for a given data object."""

    class_: Literal[
        enums.FMUClass.surface,
        enums.FMUClass.table,
        enums.FMUClass.cpgrid,
        enums.FMUClass.cpgrid_property,
        enums.FMUClass.polygons,
        enums.FMUClass.cube,
        enums.FMUClass.well,
        enums.FMUClass.points,
        enums.FMUClass.dictionary,
    ] = Field(
        alias="class",
        title="metadata_class",
    )
    """The class of the data object being exported and described by the metadata
    contained herein."""

    fmu: FMUAttributes
    """The ``fmu`` block contains all attributes specific to FMU.
    See :class:`FMUAttributes`."""

    access: SsdlAccess
    """The ``access`` block contains information related to access control for
    this data object. See :class:`SsdlAccess`."""

    data: content.AnyData
    """The ``data`` block contains information about the data contained in this
    object. See :class:`content.AnyData`."""

    file: File
    """ The ``file`` block contains references to this data object as a file on a disk.
    See :class:`File`."""

    display: Display
    """ The ``display`` block contains information related to how this data object
    should/could be displayed. See :class:`Display`."""


class Root(
    RootModel[
        Annotated[
            Union[
                CaseMetadata,
                ObjectMetadata,
            ],
            Field(discriminator="class_"),
        ]
    ]
):
    @model_validator(mode="after")
    def _check_class_data_spec(self) -> Root:
        if (
            self.root.class_ in (enums.FMUClass.table, enums.FMUClass.surface)
            and hasattr(self.root, "data")
            and self.root.data.root.spec is None
        ):
            raise ValueError(
                "When 'class' is 'table' or 'surface', "
                "'data' must contain the 'spec' field."
            )
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> Dict[str, object]:
        json_schema = super().__get_pydantic_json_schema__(core_schema, handler)
        json_schema = handler.resolve_ref_schema(json_schema)
        json_schema.update(
            {
                "if": {"properties": {"class": {"enum": ["table", "surface"]}}},
                "then": {"properties": {"data": {"required": ["spec"]}}},
            }
        )
        return json_schema


def _remove_discriminator_mapping(obj: Dict) -> Dict:
    """
    Modifies a provided JSON schema object by specifically
    removing the `discriminator.mapping` fields. This alteration aims
    to ensure compatibility with the AJV Validator by addressing and
    resolving schema validation errors that previously led to startup
    failures in applications like `sumo-core`.
    """
    del obj["discriminator"]["mapping"]
    del obj["$defs"]["AnyData"]["discriminator"]["mapping"]
    return obj


def _remove_format_path(obj: T) -> T:
    """
    Removes entries with key "format" and value "path" from dictionaries. This
    adjustment is necessary because JSON Schema does not recognize the "format":
    "path", while OpenAPI does. This function is used in contexts where OpenAPI
    specifications are not applicable.
    """

    if isinstance(obj, dict):
        return {
            k: _remove_format_path(v)
            for k, v in obj.items()
            if not (k == "format" and v == "path")
        }

    if isinstance(obj, list):
        return [_remove_format_path(element) for element in obj]

    return obj


def dump() -> Dict:
    """
    Dumps the export root model to JSON format for schema validation and
    usage in FMU data structures.

    To update the schema:
        1. Run the following CLI command to dump the updated schema:
            `python3 -m fmu.dataio.datastructure.meta > schema/definitions/0.8.0/schema/fmu_meta.json`
        2. Check the diff for changes. Adding fields usually indicates non-breaking
            changes and is generally safe. However, if fields are removed, it could
            indicate breaking changes that may affect dependent systems. Perform a
            quality control (QC) check to ensure these changes do not break existing
            implementations.
            If changes are satisfactory and do not introduce issues, commit
            them to maintain schema consistency.
    """  # noqa: E501
    schema = dict(
        ChainMap(
            {
                "$contractual": [
                    "access",
                    "class",
                    "data.alias",
                    "data.bbox",
                    "data.content",
                    "data.format",
                    "data.grid_model",
                    "data.is_observation",
                    "data.is_prediction",
                    "data.name",
                    "data.offset",
                    "data.seismic.attribute",
                    "data.spec.columns",
                    "data.stratigraphic",
                    "data.stratigraphic_alias",
                    "data.tagname",
                    "data.time",
                    "data.vertical_domain",
                    "file.checksum_md5",
                    "file.relative_path",
                    "file.size_bytes",
                    "fmu.aggregation.operation",
                    "fmu.aggregation.realization_ids",
                    "fmu.case",
                    "fmu.context.stage",
                    "fmu.iteration.name",
                    "fmu.iteration.uuid",
                    "fmu.model",
                    "fmu.realization.id",
                    "fmu.realization.name",
                    "fmu.realization.uuid",
                    "fmu.workflow",
                    "masterdata",
                    "source",
                    "tracklog.datetime",
                    "tracklog.event",
                    "tracklog.user.id",
                    "version",
                ],
                # schema must be present for "dependencies" key to work.
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "fmu_meta.json",
            },
            Root.model_json_schema(),
        )
    )

    return _remove_format_path(
        _remove_discriminator_mapping(
            schema,
        ),
    )
