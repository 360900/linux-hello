from pathlib import PurePath
import paths

models = [
    "shape_predictor_5_face_landmarks.dat",
    "mmod_human_face_detector.dat",
    "dlib_face_recognition_resnet_model_v1.dat",
]


def dlib_data_dir_path() -> str:
    return str(paths.dlib_data_dir)


def shape_predictor_5_face_landmarks_path() -> str:
    return str(paths.dlib_data_dir / models[0])


def mmod_human_face_detector_path() -> str:
    return str(paths.dlib_data_dir / models[1])


def dlib_face_recognition_resnet_model_v1_path() -> str:
    return str(paths.dlib_data_dir / models[2])


def snapshots_dir_path() -> PurePath:
    return paths.log_path / "snapshots"


def snapshot_path(snapshot: str) -> str:
    return str(snapshots_dir_path() / snapshot)


def logo_path() -> str:
    return str(paths.data_dir / "logo.png")


def webauthn_dir_path() -> str:
    return str(paths.config_dir / "webauthn")


def webauthn_state_path() -> str:
    return str(paths.config_dir / "webauthn" / "state.json")


def webauthn_keystore_dir_path() -> str:
    return str(paths.config_dir / "webauthn" / "keystore")


def webauthn_credentials_path(user: str) -> str:
    return str(paths.config_dir / "webauthn" / "credentials" / f"{user}.json")
