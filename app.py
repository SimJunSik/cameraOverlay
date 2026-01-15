import logging
import os
import plistlib
import sys
from dataclasses import dataclass
from ctypes import c_void_p

import cv2
import mediapipe as mp
import numpy as np
from PyQt6.QtCore import QTimer, Qt, QRect
from PyQt6.QtGui import QImage, QPixmap, QRegion
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)


@dataclass
class AppConfig:
    width: int = 320
    height: int = 320
    blur_kernel: int = 21
    zoom_min: int = 100
    zoom_max: int = 300


class CameraOverlay(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.shape = "square"
        self.cutout_enabled = False
        self._dragging = False
        self._drag_pos = None
        self.camera_error_message = None
        self._permission_requested = False
        self._open_retry_counter = 0
        self.debug_text_path = os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Logs",
            "CameraOverlay.last.txt",
        )

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(self.config.width, self.config.height + 44)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFixedSize(self.config.width, self.config.height)
        self.label.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.controls = QWidget(self)
        self.controls.setObjectName("controls")
        self.controls.setFixedHeight(44)
        self.controls.setStyleSheet(
            "#controls { background-color: rgba(0, 0, 0, 160); border-radius: 8px; }"
        )
        self.controls.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.shape_button = QPushButton("모양: 네모", self.controls)
        self.shape_button.clicked.connect(self.toggle_shape)
        self.shape_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.cutout_button = QPushButton("배경 제거: OFF", self.controls)
        self.cutout_button.clicked.connect(self.toggle_cutout)
        self.cutout_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.zoom_label = QLabel("줌 2.0x", self.controls)
        self.zoom_label.setStyleSheet("color: white;")
        self.zoom_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.zoom_bar = QScrollBar(Qt.Orientation.Horizontal, self.controls)
        self.zoom_bar.setMinimum(self.config.zoom_min)
        self.zoom_bar.setMaximum(self.config.zoom_max)
        self.zoom_bar.setValue(200)
        self.zoom_bar.setSingleStep(5)
        self.zoom_bar.setPageStep(10)
        self.zoom_bar.valueChanged.connect(self.on_zoom_change)
        self.zoom_bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.close_button = QPushButton("닫기", self.controls)
        self.close_button.clicked.connect(self.close)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        controls_layout = QHBoxLayout(self.controls)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.shape_button)
        controls_layout.addWidget(self.cutout_button)
        controls_layout.addWidget(self.zoom_label)
        controls_layout.addWidget(self.zoom_bar, 1)
        controls_layout.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.controls, 0)

        self._ensure_camera_permission()
        if self._validate_camera_usage_description():
            self.cap = self._open_camera()
        else:
            self.cap = None
        self._configure_mediapipe_resource_dir()
        self.segmenter = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(33)

        self.top_timer = QTimer(self)
        self.top_timer.timeout.connect(self.ensure_on_top)
        self.top_timer.start(1000)

        self.move_to_top_center()
        self.apply_shape_mask()

    def move_to_top_center(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        rect = screen.availableGeometry()
        x = rect.x() + (rect.width() - self.config.width) // 2
        y = rect.y()
        self.move(x, y)

    def apply_shape_mask(self) -> None:
        if self.shape == "circle":
            region = QRegion(
                QRect(0, 0, self.label.width(), self.label.height()),
                QRegion.RegionType.Ellipse,
            )
            self.label.setMask(region)
        else:
            self.label.clearMask()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.childAt(event.position().toPoint()) is self.label:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def update_frame(self) -> None:
        try:
            if not self.cap or not self.cap.isOpened():
                self._open_retry_counter += 1
                if self._open_retry_counter % 30 == 0:
                    self._ensure_camera_permission()
                    self.cap = self._open_camera()
                self._render_placeholder(self.camera_error_message or "카메라를 열 수 없습니다.")
                return

            success, frame = self.cap.read()
            if not success:
                return

            frame = cv2.flip(frame, 1)
            target_w = self.label.width()
            target_h = self.label.height()
            frame = self._apply_zoom(frame)
            frame = cv2.resize(frame, (target_w, target_h))
            if self.cutout_enabled:
                if not self._ensure_segmenter_ready():
                    return
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = self.segmenter.process(rgb)
                mask = results.segmentation_mask if results else None
                if mask is None:
                    return
                alpha = (mask > 0.5).astype(np.uint8) * 255
                rgba = np.dstack((rgb, alpha))
                h, w, ch = rgba.shape
                bytes_per_line = ch * w
                image = QImage(rgba.data, w, h, bytes_per_line, QImage.Format.Format_RGBA8888)
            else:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w
                image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)

            self.label.setPixmap(QPixmap.fromImage(image))
        except Exception:
            logging.exception("update_frame failed")
            self._render_placeholder("오류가 발생했습니다. 로그를 확인하세요.")

    def closeEvent(self, event) -> None:
        if self.cap and self.cap.isOpened():
            self.cap.release()
        if self.segmenter:
            self.segmenter.close()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.ensure_on_top()
        QTimer.singleShot(150, self.ensure_on_top)

    def _open_camera(self) -> cv2.VideoCapture:
        if self._camera_permission_denied():
            self.camera_error_message = "카메라 권한이 꺼져 있습니다. 시스템 설정에서 허용하세요."
            return None
        cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        if cap.isOpened():
            return cap
        cap.release()
        fallback = cv2.VideoCapture(0)
        if not fallback.isOpened():
            self.camera_error_message = "카메라 권한을 확인해 주세요."
            return None
        return fallback

    def _ensure_camera_permission(self) -> None:
        if sys.platform != "darwin":
            return
        try:
            from AVFoundation import (
                AVCaptureDevice,
                AVMediaTypeVideo,
                AVAuthorizationStatusAuthorized,
                AVAuthorizationStatusDenied,
                AVAuthorizationStatusNotDetermined,
                AVAuthorizationStatusRestricted,
            )
        except Exception:
            self.camera_error_message = (
                "AVFoundation 모듈을 불러올 수 없습니다. 앱을 다시 빌드하세요."
            )
            return
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo)
        if status == AVAuthorizationStatusAuthorized:
            return
        if status in (AVAuthorizationStatusDenied, AVAuthorizationStatusRestricted):
            self.camera_error_message = "카메라 권한이 꺼져 있습니다. 시스템 설정에서 허용하세요."
            return
        if status == AVAuthorizationStatusNotDetermined and not self._permission_requested:
            self._permission_requested = True

            def _handler(granted: bool) -> None:
                if granted:
                    logging.info("camera permission granted")
                else:
                    logging.info("camera permission denied")

            AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVMediaTypeVideo, _handler
            )

    def _camera_permission_denied(self) -> bool:
        if sys.platform != "darwin":
            return False
        try:
            from AVFoundation import (
                AVCaptureDevice,
                AVMediaTypeVideo,
                AVAuthorizationStatusDenied,
                AVAuthorizationStatusRestricted,
            )
        except Exception:
            self.camera_error_message = (
                "AVFoundation 모듈을 불러올 수 없습니다. 앱을 다시 빌드하세요."
            )
            return False
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo)
        return status in (AVAuthorizationStatusDenied, AVAuthorizationStatusRestricted)

    def toggle_shape(self) -> None:
        self.shape = "circle" if self.shape == "square" else "square"
        self.shape_button.setText("모양: 원" if self.shape == "circle" else "모양: 네모")
        self.apply_shape_mask()

    def toggle_cutout(self) -> None:
        if not self.cutout_enabled:
            if not self._ensure_segmenter_ready():
                return
            self.cutout_enabled = True
        else:
            self.cutout_enabled = False
        self.cutout_button.setText("배경 제거: ON" if self.cutout_enabled else "배경 제거: OFF")

    def on_zoom_change(self, value: int) -> None:
        zoom = value / 100.0
        self.zoom_label.setText(f"줌 {zoom:.1f}x")

    def _apply_zoom(self, frame: np.ndarray) -> np.ndarray:
        zoom = self.zoom_bar.value() / 100.0
        if zoom <= 1.0:
            return frame
        h, w = frame.shape[:2]
        new_w = max(1, int(w / zoom))
        new_h = max(1, int(h / zoom))
        x1 = (w - new_w) // 2
        y1 = (h - new_h) // 2
        cropped = frame[y1 : y1 + new_h, x1 : x1 + new_w]
        return cropped

    def _configure_mediapipe_resource_dir(self) -> None:
        if sys.platform != "darwin" or not getattr(sys, "frozen", False):
            return
        resources_dir = os.path.abspath(os.path.join(sys.executable, "..", "..", "Resources"))
        frameworks_dir = os.path.abspath(os.path.join(sys.executable, "..", "..", "Frameworks"))
        candidate_dirs = [
            resources_dir,
            frameworks_dir,
        ]
        try:
            from mediapipe.python._framework_bindings import resource_util
        except Exception:
            logging.exception("mediapipe resource_util import failed")
            return
        for base_dir in candidate_dirs:
            modules_dir = os.path.join(base_dir, "mediapipe", "modules")
            if os.path.isdir(modules_dir):
                resource_util.set_resource_dir(base_dir)
                logging.info("mediapipe resource dir set to %s", base_dir)
                return

    def _ensure_segmenter_ready(self) -> bool:
        if self.segmenter:
            return True
        try:
            self._configure_mediapipe_resource_dir()
            if sys.platform == "darwin" and getattr(sys, "frozen", False):
                resources_dir = os.path.abspath(os.path.join(sys.executable, "..", "..", "Resources"))
                frameworks_dir = os.path.abspath(os.path.join(sys.executable, "..", "..", "Frameworks"))
                candidate_dirs = [resources_dir, frameworks_dir]
                binary_relpath = os.path.join(
                    "mediapipe",
                    "modules",
                    "selfie_segmentation",
                    "selfie_segmentation_cpu.binarypb",
                )
                found_base = None
                for base_dir in candidate_dirs:
                    if os.path.isfile(os.path.join(base_dir, binary_relpath)):
                        found_base = base_dir
                        break
                if found_base:
                    from mediapipe.python._framework_bindings import resource_util
                    resource_util.set_resource_dir(found_base)
                    logging.info("mediapipe resource dir forced to %s", found_base)
                else:
                    self.camera_error_message = (
                        "mediapipe model not found.\n"
                        f"searched: {resources_dir}\n"
                        f"searched: {frameworks_dir}\n"
                        "rebuild the app."
                    )
                    self._render_placeholder(self.camera_error_message)
                    return False

            self.segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
            return True
        except FileNotFoundError:
            self.camera_error_message = (
                "mediapipe model missing.\n"
                "rebuild the app."
            )
            self._render_placeholder(self.camera_error_message)
            return False
        except Exception:
            logging.exception("segmenter init failed")
            self.camera_error_message = "배경 제거 초기화에 실패했습니다."
            self._render_placeholder(self.camera_error_message)
            return False

    def _validate_camera_usage_description(self) -> bool:
        if sys.platform != "darwin":
            return True
        if not getattr(sys, "frozen", False):
            return True
        info_path = self._get_info_plist_path()
        try:
            with open(info_path, "rb") as handle:
                info = plistlib.load(handle)
        except Exception:
            logging.exception("Info.plist read failed: %s", info_path)
            self.camera_error_message = f"Info.plist를 읽을 수 없습니다: {info_path}"
            return False
        if not info.get("NSCameraUsageDescription"):
            self.camera_error_message = f"카메라 권한 설명이 없습니다: {info_path}"
            return False
        return True

    def _get_info_plist_path(self) -> str:
        if sys.platform == "darwin":
            try:
                from Foundation import NSBundle
            except Exception:
                return os.path.abspath(os.path.join(sys.executable, "..", "..", "Info.plist"))
            bundle_path = NSBundle.mainBundle().bundlePath()
            if bundle_path:
                return os.path.join(bundle_path, "Contents", "Info.plist")
        return os.path.abspath(os.path.join(sys.executable, "..", "..", "Info.plist"))

    def _render_placeholder(self, message: str) -> None:
        self._write_debug_text(message)
        width = self.label.width()
        height = self.label.height()
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        lines = message.splitlines() if message else ["error"]
        y = 24
        for line in lines[:6]:
            cv2.putText(frame, line[:64], (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            y += 26
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        self.label.setPixmap(QPixmap.fromImage(image))

    def _write_debug_text(self, message: str) -> None:
        try:
            with open(self.debug_text_path, "w", encoding="utf-8") as handle:
                handle.write(message or "")
        except Exception:
            logging.exception("failed to write debug text")

    def ensure_on_top(self) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        if sys.platform != "darwin":
            return
        try:
            import objc
            from AppKit import (
                NSScreenSaverWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
            )
        except Exception:
            return
        try:
            view = objc.objc_object(c_void_p(int(self.winId())))
            window = view.window()
            if window is None:
                return
            window.setHidesOnDeactivate_(False)
            window.setLevel_(NSScreenSaverWindowLevel)
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            window.orderFront_(None)
        except Exception:
            return


def main() -> int:
    log_path = os.path.join(os.path.expanduser("~"), "Library", "Logs", "CameraOverlay.log")
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    def _log_exception(exctype, value, tb) -> None:
        logging.exception("Unhandled exception", exc_info=(exctype, value, tb))

    sys.excepthook = _log_exception

    app = QApplication(sys.argv)
    config = AppConfig()
    overlay = CameraOverlay(config)
    overlay.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
