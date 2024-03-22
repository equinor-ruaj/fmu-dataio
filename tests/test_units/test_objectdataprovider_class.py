"""Test the _ObjectData class from the _objectdata.py module"""

import pytest
from fmu.dataio._definitions import ConfigurationError, ValidFormats
from fmu.dataio.providers._objectdata import objectdata_provider_factory

# --------------------------------------------------------------------------------------
# RegularSurface
# --------------------------------------------------------------------------------------


def test_objectdata_regularsurface_derive_name_stratigraphy(regsurf, edataobj1):
    """Get name and some stratigaphic keys for a valid RegularSurface object ."""
    # mimic the stripped parts of configuations for testing here
    objdata = objectdata_provider_factory(regsurf, edataobj1)

    res = objdata._derive_name_stratigraphy()

    assert res.name == "Whatever Top"
    assert "TopWhatever" in res.alias
    assert res.stratigraphic is True


def test_objectdata_regularsurface_derive_name_stratigraphy_differ(regsurf, edataobj2):
    """Get name and some stratigaphic keys for a valid RegularSurface object ."""
    # mimic the stripped parts of configuations for testing here
    objdata = objectdata_provider_factory(regsurf, edataobj2)

    res = objdata._derive_name_stratigraphy()

    assert res.name == "VOLANTIS GP. Top"
    assert "TopVolantis" in res.alias
    assert res.stratigraphic is True


def test_objectdata_regularsurface_validate_extension(regsurf, edataobj1):
    """Test a valid extension for RegularSurface object."""

    ext = objectdata_provider_factory(regsurf, edataobj1)._validate_get_ext(
        "irap_binary", "RegularSurface", ValidFormats().surface
    )

    assert ext == ".gri"


def test_objectdata_regularsurface_validate_extension_shall_fail(regsurf, edataobj1):
    """Test an invalid extension for RegularSurface object."""

    with pytest.raises(ConfigurationError):
        objectdata_provider_factory(regsurf, edataobj1)._validate_get_ext(
            "some_invalid", "RegularSurface", ValidFormats().surface
        )


def test_objectdata_regularsurface_spec_bbox(regsurf, edataobj1):
    """Derive specs and bbox for RegularSurface object."""

    objdata = objectdata_provider_factory(regsurf, edataobj1)
    specs = objdata.get_spec()
    bbox = objdata.get_bbox()

    assert specs["ncol"] == regsurf.ncol
    assert bbox["xmin"] == 0.0
    assert bbox["zmin"] == 1234.0


def test_objectdata_regularsurface_derive_objectdata(regsurf, edataobj1):
    """Derive other properties."""

    res = objectdata_provider_factory(regsurf, edataobj1).get_objectdata()

    assert res.subtype == "RegularSurface"
    assert res.classname == "surface"
    assert res.extension == ".gri"


def test_objectdata_regularsurface_derive_metadata(regsurf, edataobj1):
    """Derive all metadata for the 'data' block in fmu-dataio."""

    myobj = objectdata_provider_factory(regsurf, edataobj1)
    myobj.derive_metadata()
    res = myobj.metadata
    assert res["content"] == "depth"

    assert res["alias"]
