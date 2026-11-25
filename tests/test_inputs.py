import pytest
from academic_timetabling_demo.domain import TABLES, DataValidationError

from tests.conftest import rezip, unzip
from timetabling_web.inputs import SAMPLES, UploadError, read_zip, sample_instance, sample_zip


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_metadata_matches_the_generated_instance(name):
    assert len(sample_instance(name).sections) == SAMPLES[name].sections


def test_sample_zip_has_every_table_and_reads_back_to_the_same_instance():
    content = sample_zip("demo")

    assert len(unzip(content)) == len(TABLES) + 1
    instance, notes = read_zip(content)
    assert instance == sample_instance("demo")
    assert notes == ()


def test_files_may_sit_at_the_root_or_inside_a_folder():
    files = unzip(sample_zip("micro"))

    assert read_zip(rezip(files))[0] == read_zip(rezip(files, "carpeta/sub/"))[0]


def test_missing_config_uses_the_default_weights_and_says_so():
    files = unzip(sample_zip("micro"))
    del files["config.json"]

    _, notes = read_zip(rezip(files))

    assert "config.json" in notes[0]


def test_paths_inside_the_zip_are_never_used_to_write():
    files = unzip(sample_zip("micro"))
    hostile = rezip({"../../../tmp/evil.csv": b"x", **{"../" + k: v for k, v in files.items()}})

    instance, _ = read_zip(hostile)  # se toma solo el nombre base de los archivos esperados

    assert len(instance.sections) == 3


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "vacío"),
        (b"esto no es un zip", "no es un ZIP"),
        (rezip({"otra_cosa.txt": b"hola"}), "ningún archivo"),
    ],
)
def test_unacceptable_uploads_are_rejected_with_a_message(content, message):
    with pytest.raises(UploadError, match=message):
        read_zip(content)


def test_duplicate_files_are_rejected():
    files = unzip(sample_zip("micro"))
    content = rezip({**{"a/" + k: v for k, v in files.items()}, "b/rooms.csv": files["rooms.csv"]})

    with pytest.raises(UploadError, match="más de una vez"):
        read_zip(content)


def test_broken_data_reports_the_file_and_row():
    files = unzip(sample_zip("micro"))
    files["rooms.csv"] = files["rooms.csv"].replace(b",", b";", 1)

    with pytest.raises(DataValidationError) as error:
        read_zip(rezip(files))

    assert any(issue.file == "rooms.csv" for issue in error.value.issues)
