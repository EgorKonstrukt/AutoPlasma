import sys
import os
import json
import requests
import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional, List, Deque, Any
from collections import deque
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit,
                             QMessageBox, QHeaderView, QGroupBox, QFormLayout, QInputDialog,
                             QDialog, QFrame, QScrollArea, QTabWidget, QGridLayout,
                             QMenuBar, QMenu, QStatusBar, QFileDialog, QSpinBox,
                             QDoubleSpinBox, QCheckBox, QDialogButtonBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QElapsedTimer, QSettings, QSize, QPoint
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QIcon, QKeySequence, QActionGroup, QAction

import pyqtgraph as pg

# --- Конфигурация и Логирование ---
CONFIG_FILE = "client_config.json"
LOG_FILE = "client.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("AutoPlasmaClient")


@dataclass
class ClientConfig:
    operator_name: str = "Operator"
    theme: str = "Dark"
    window_geometry: Optional[List[int]] = None
    auto_save_logs: bool = True
    graph_history_size: int = 200
    api_url: str = "http://127.0.0.1:8000"

    @staticmethod
    def load() -> 'ClientConfig':
        if not os.path.exists(CONFIG_FILE):
            logger.info("Config file not found. Creating default.")
            return ClientConfig()
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                # Merge with defaults to handle new fields in future versions
                return ClientConfig(**{k: v for k, v in data.items() if k in ClientConfig.__annotations__})
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return ClientConfig()

    def save(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(asdict(self), f, indent=4)
            logger.info("Configuration saved.")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")


STYLESHEET = """
QPushButton:hover { background-color: #465f75; }
QPushButton:pressed { background-color: #2c3e50; }
QPushButton:disabled { background-color: #2c3e50; color: #7f8c8d; }
QPushButton#btnStart { background-color: #27ae60; font-weight: bold; }
QPushButton#btnStart:hover { background-color: #2ecc71; }
QPushButton#btnStop { background-color: #c0392b; font-weight: bold; }
QPushButton#btnStop:hover { background-color: #e74c3c; }
QPushButton#btnStats { background-color: #2980b9; }
QLabel { color: #ecf0f1; }
QLabel#bigValue { font-size: 24px; font-weight: bold; color: #f1c40f; }
QLabel#statusOk { color: #2ecc71; font-weight: bold; }
QLabel#statusLow { color: #e74c3c; font-weight: bold; }
"""


# --- Сетевой менеджер ---
class NetworkManager:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def get_powders(self) -> List['PowderData']:
        try:
            resp = requests.get(f"{self.base_url}/powders/", timeout=5)
            resp.raise_for_status()
            return [PowderData(**item) for item in resp.json()]
        except Exception as e:
            logger.error(f"Network error (get_powders): {e}")
            return []

    def get_stock(self) -> List['StockData']:
        try:
            resp = requests.get(f"{self.base_url}/inventory/", timeout=5)
            resp.raise_for_status()
            return [StockData(**item) for item in resp.json()]
        except Exception as e:
            logger.error(f"Network error (get_stock): {e}")
            return []

    def get_logs(self, limit=50):
        try:
            resp = requests.get(f"{self.base_url}/logs/", params={"limit": limit}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Network error (get_logs): {e}")
            return []

    def log_usage(self, name: str, grams: float, duration: float, op: str) -> bool:
        try:
            resp = requests.post(f"{self.base_url}/log_usage/",
                                 json={"powder_name": name, "consumed_grams": grams, "duration_sec": duration,
                                       "operator": op}, timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Network error (log_usage): {e}")
            return False

    def add_powder(self, name: str, density: float, factor: float, gpm: float) -> bool:
        try:
            resp = requests.post(f"{self.base_url}/powders/",
                                 json={"name": name, "density": density, "flow_factor": factor, "target_gpm": gpm},
                                 timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Network error (add_powder): {e}")
            return False


# --- Модели данных ---
@dataclass
class PowderData:
    id: int
    name: str
    density: float
    flow_factor: float
    target_gpm: float


@dataclass
class StockData:
    id: int
    powder_id: int
    powder_name: str
    quantity_grams: float


# --- Потоки и Виджеты ---
class HardwareSimulator(QThread):
    status_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.running = False
        self.rpm = 0.0
        self._timer = QElapsedTimer()
        self._current_time = 0.0

    def start_dosage(self, rpm: float):
        self.rpm = rpm
        self.running = True
        self._current_time = 0.0
        self._timer.start()
        self.start()
        logger.info(f"Hardware simulation started at {rpm} RPM")

    def stop_dosage(self):
        self.running = False
        self.wait(1000)
        if self.isRunning():
            self.terminate()
        self.finished_signal.emit(self._current_time)
        logger.info("Hardware simulation stopped")

    def run(self):
        while self.running:
            self._current_time = self._timer.elapsed() / 1000.0
            noise = (hash(str(time.time())) % 100) / 100.0 - 0.5
            current_rpm = self.rpm + noise

            self.status_signal.emit({
                "time": self._current_time,
                "rpm": current_rpm,
                "status": "running"
            })
            self.msleep(200)

        self.status_signal.emit({"time": self._current_time, "rpm": 0, "status": "stopped"})


class CircularProgress(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(150, 150)
        self.value = 0.0
        self.color = QColor("#7f8c8d")

    def set_value(self, val: float, color_str: str = "#7f8c8d"):
        self.value = max(0.0, min(1.0, val))
        self.color = QColor(color_str)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(10, 10, -10, -10)
        center = rect.center()
        radius = min(rect.width(), rect.height()) / 2

        painter.setPen(QPen(QColor("#34495e"), 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)

        if self.value > 0:
            painter.setPen(QPen(self.color, 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            span = int(self.value * 360 * 16)
            painter.drawArc(rect, -90 * 16, span)

        painter.setPen(QColor("white"))
        font = QFont("Arial", 14, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{int(self.value * 100)}%")


class SettingsDialog(QDialog):
    def __init__(self, config: ClientConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Application Settings")
        self.setStyleSheet(STYLESHEET)
        self.setModal(True)
        self.setGeometry(100, 100, 400, 300)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.op_input = QLineEdit(self.config.operator_name)
        self.op_input.setPlaceholderText("Enter default operator name")
        form.addRow("Default Operator:", self.op_input)

        self.api_input = QLineEdit(self.config.api_url)
        form.addRow("API URL:", self.api_input)

        self.graph_size = QSpinBox()
        self.graph_size.setRange(50, 1000)
        self.graph_size.setValue(self.config.graph_history_size)
        form.addRow("Graph History Points:", self.graph_size)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.save_settings)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def save_settings(self):
        self.config.operator_name = self.op_input.text().strip() or "Operator"
        self.config.api_url = self.api_input.text().strip() or "http://127.0.0.1:8000"
        self.config.graph_history_size = self.graph_size.value()
        self.config.save()
        logger.info("Settings updated by user")
        self.accept()


class StatsDialog(QDialog):
    def __init__(self, net_manager: NetworkManager, parent=None):
        super().__init__(parent)
        self.net = net_manager
        self.setWindowTitle("Production Statistics")
        self.setStyleSheet(STYLESHEET)
        self.setGeometry(100, 100, 900, 600)
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self.create_stock_widget(), "Warehouse Stock")
        tabs.addTab(self.create_log_widget(), "Usage History")
        layout.addWidget(tabs)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)
        self.refresh_data()

    def create_stock_widget(self):
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Powder", "Stock (g)", "Status"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stock_table_ref = table
        return table

    def create_log_widget(self):
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Time", "Powder", "Used (g)", "Duration", "Operator"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.log_table_ref = table
        return table

    def refresh_data(self):
        stock = self.net.get_stock()
        t = self.stock_table_ref
        t.setRowCount(len(stock))
        for i, s in enumerate(stock):
            t.setItem(i, 0, QTableWidgetItem(s.powder_name))
            t.setItem(i, 1, QTableWidgetItem(f"{s.quantity_grams:.1f}"))
            status = "OK" if s.quantity_grams > 500 else "LOW"
            item = QTableWidgetItem(status)
            item.setForeground(QColor("#2ecc71" if s.quantity_grams > 500 else "#e74c3c"))
            t.setItem(i, 2, item)

        logs = self.net.get_logs()
        t = self.log_table_ref
        t.setRowCount(len(logs))
        for i, l in enumerate(logs):
            t.setItem(i, 0, QTableWidgetItem(l['timestamp'][:19]))
            t.setItem(i, 1, QTableWidgetItem(l['powder_name']))
            change = -l['consumed_grams']
            item_val = QTableWidgetItem(f"{change:.2f}")
            item_val.setForeground(QColor("#2ecc71" if change > 0 else "#e74c3c"))
            t.setItem(i, 2, item_val)
            t.setItem(i, 3, QTableWidgetItem(f"{l['duration_sec']:.1f}s"))
            t.setItem(i, 4, QTableWidgetItem(l['operator']))


class PlasmaClientEnhanced(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = ClientConfig.load()
        self.net = NetworkManager(self.config.api_url)

        self.setWindowTitle(f"AutoPlasma Client - Operator: {self.config.operator_name}")
        self.setStyleSheet(STYLESHEET)

        # Restore geometry
        if self.config.window_geometry:
            self.setGeometry(*self.config.window_geometry)
        else:
            self.setGeometry(100, 100, 1200, 800)

        self.hw = HardwareSimulator()
        self.hw.status_signal.connect(self.update_live_data)
        self.hw.finished_signal.connect(self.on_process_finished)

        self.selected_powder: Optional[PowderData] = None
        self.current_rpm = 0.0

        self.data_buffer_x: Deque[float] = deque(maxlen=self.config.graph_history_size)
        self.data_buffer_y: Deque[float] = deque(maxlen=self.config.graph_history_size)

        self.init_ui()
        self.refresh_powder_list()

        self.timer = QTimer()
        self.timer.timeout.connect(self.background_update)
        self.timer.start(5000)

        logger.info("Client initialized successfully")

    def init_ui(self):
        self._create_menu_bar()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Left Panel
        left_panel = QGroupBox("Material Selection")
        left_layout = QVBoxLayout(left_panel)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Density", "Factor", "Target"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_selection_change)
        left_layout.addWidget(self.table)

        btn_add = QPushButton("Add New Material")
        btn_add.clicked.connect(self.add_material_dialog)
        left_layout.addWidget(btn_add)

        # Center Panel
        center_panel = QGroupBox("Process Control")
        center_layout = QVBoxLayout(center_panel)

        top_indicators = QHBoxLayout()
        self.progress_ring = CircularProgress()
        top_indicators.addWidget(self.progress_ring)

        readouts = QGridLayout()
        self.lbl_rpm_val = QLabel("0.0")
        self.lbl_rpm_val.setObjectName("bigValue")
        self.lbl_time_val = QLabel("0.0s")
        self.lbl_time_val.setObjectName("bigValue")
        self.lbl_mass_val = QLabel("0.0g")
        self.lbl_mass_val.setObjectName("bigValue")

        readouts.addWidget(QLabel("Current RPM:"), 0, 0)
        readouts.addWidget(self.lbl_rpm_val, 0, 1)
        readouts.addWidget(QLabel("Duration:"), 1, 0)
        readouts.addWidget(self.lbl_time_val, 1, 1)
        readouts.addWidget(QLabel("Est. Used:"), 2, 0)
        readouts.addWidget(self.lbl_mass_val, 2, 1)

        top_indicators.addLayout(readouts, 1)
        center_layout.addLayout(top_indicators)

        self.plot_widget = pg.PlotWidget(background='#2c3e50')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('left', 'Motor Speed', units='RPM')
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.setYRange(0, 100)
        self.plot_widget.setXRange(0, 60)

        self.curve = self.plot_widget.plot(pen=pg.mkPen('#f1c40f', width=2))
        center_layout.addWidget(self.plot_widget)

        ctrl_layout = QHBoxLayout()
        self.btn_calc = QPushButton("Calculate Parameters")
        self.btn_start = QPushButton("START PROCESS")
        self.btn_start.setObjectName("btnStart")
        self.btn_stop = QPushButton("STOP & LOG")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stats = QPushButton("Statistics")
        self.btn_stats.setObjectName("btnStats")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)

        self.btn_calc.clicked.connect(self.calculate_params)
        self.btn_start.clicked.connect(self.start_process)
        self.btn_stop.clicked.connect(self.stop_process)
        self.btn_stats.clicked.connect(self.show_stats)

        ctrl_layout.addWidget(self.btn_calc)
        ctrl_layout.addWidget(self.btn_start)
        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addWidget(self.btn_stats)
        center_layout.addLayout(ctrl_layout)

        # Right Panel
        right_panel = QGroupBox("System Status")
        right_layout = QVBoxLayout(right_panel)

        self.lbl_sel_info = QLabel("No material selected")
        self.lbl_sel_info.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.lbl_stock_info = QLabel("Stock: --")
        self.lbl_stock_info.setFont(QFont("Arial", 10))

        self.lbl_sys_log = QLabel("System Ready")
        self.lbl_sys_log.setStyleSheet(
            "color: #95a5a6; border: 1px solid #34495e; padding: 10px; border-radius: 4px; background-color: #22303d;")
        self.lbl_sys_log.setWordWrap(True)
        self.lbl_sys_log.setMinimumHeight(100)

        form = QFormLayout()
        form.addRow("Selection:", self.lbl_sel_info)
        form.addRow("Availability:", self.lbl_stock_info)

        right_layout.addLayout(form)
        right_layout.addWidget(QLabel("System Log:"))
        right_layout.addWidget(self.lbl_sys_log)
        right_layout.addStretch()

        main_layout.addWidget(left_panel, 2)
        main_layout.addWidget(center_panel, 2)
        main_layout.addWidget(right_panel, 1)

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage(f"Connected to: {self.config.api_url} | Operator: {self.config.operator_name}")

    def _create_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")

        act_settings = QAction("Settings...", self)
        act_settings.setShortcut(QKeySequence.StandardKey.Preferences)
        act_settings.triggered.connect(self.open_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_exit = QAction("Exit", self)
        act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        help_menu = menubar.addMenu("Help")
        act_about = QAction("About", self)
        act_about.triggered.connect(self.show_about)
        help_menu.addAction(act_about)

    def open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Apply changes
            self.net.base_url = self.config.api_url
            self.setWindowTitle(f"AutoPlasma Client - Operator: {self.config.operator_name}")
            self.statusBar.showMessage(f"Connected to: {self.config.api_url} | Operator: {self.config.operator_name}")

            # Update graph buffer size
            max_len = self.config.graph_history_size
            self.data_buffer_x = deque(self.data_buffer_x, maxlen=max_len)
            self.data_buffer_y = deque(self.data_buffer_y, maxlen=max_len)

            logger.info("Settings applied dynamically")

    def show_about(self):
        QMessageBox.about(self, "About AutoPlasma",
                          "")

    def closeEvent(self, event):
        # Save window geometry
        self.config.window_geometry = [self.x(), self.y(), self.width(), self.height()]
        self.config.save()
        logger.info("Application closed, geometry saved")
        self.hw.stop_dosage()
        event.accept()

    def refresh_powder_list(self):
        data = self.net.get_powders()
        self.table.setRowCount(len(data))
        for i, p in enumerate(data):
            self.table.setItem(i, 0, QTableWidgetItem(str(p.id)))
            self.table.setItem(i, 1, QTableWidgetItem(p.name))
            self.table.setItem(i, 2, QTableWidgetItem(f"{p.density:.2f}"))
            self.table.setItem(i, 3, QTableWidgetItem(f"{p.flow_factor:.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(f"{p.target_gpm:.1f}"))

    def on_selection_change(self):
        items = self.table.selectedItems()
        if not items: return
        name = self.table.item(items[0].row(), 1).text()
        powders = self.net.get_powders()
        self.selected_powder = next((p for p in powders if p.name == name), None)

        if self.selected_powder:
            self.lbl_sel_info.setText(self.selected_powder.name)
            self.check_stock_status()
            self.btn_calc.setEnabled(True)
            self.reset_visuals()
            logger.debug(f"Selected powder: {self.selected_powder.name}")

    def check_stock_status(self):
        if not self.selected_powder: return
        stock = self.net.get_stock()
        current = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)

        self.lbl_stock_info.setText(f"Stock: {current:.1f} g")
        if current < 500:
            self.lbl_stock_info.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self.lbl_stock_info.setStyleSheet("color: #2ecc71; font-weight: bold;")

    def calculate_params(self):
        if not self.selected_powder: return
        rpm = round(((self.selected_powder.target_gpm / self.selected_powder.density) / 10.0) *
                    2.5 * self.selected_powder.flow_factor * 60, 2)
        self.current_rpm = rpm
        msg = f"Parameters calculated: {rpm} RPM"
        self.lbl_sys_log.setText(msg)
        self.statusBar.showMessage(msg, 5000)
        self.btn_start.setEnabled(True)
        self.reset_visuals()
        logger.info(f"Calculated RPM: {rpm} for {self.selected_powder.name}")

    def start_process(self):
        if not self.selected_powder or self.current_rpm == 0: return

        stock = self.net.get_stock()
        current = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)
        if current < 50:
            if QMessageBox.warning(self, "Critical Low Stock", "Stock critically low (<50g). Start anyway?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_calc.setEnabled(False)
        msg = "Process STARTED."
        self.lbl_sys_log.setText(msg)
        self.statusBar.showMessage(msg)
        self.progress_ring.set_value(0.8, "#2ecc71")

        self.data_buffer_x.clear()
        self.data_buffer_y.clear()
        self.plot_widget.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        self.plot_widget.enableAutoRange(axis=pg.ViewBox.XAxis, enable=True)

        self.hw.start_dosage(self.current_rpm)

    def stop_process(self):
        self.hw.stop_dosage()
        self.btn_stop.setEnabled(False)
        self.lbl_sys_log.setText("Stopping motor...")

    def on_process_finished(self, duration: float):
        self.btn_start.setEnabled(True)
        self.btn_calc.setEnabled(True)
        self.progress_ring.set_value(0.0, "#7f8c8d")

        if duration < 1.0:
            msg = "Stopped (too short, <1s). Not logged."
            self.lbl_sys_log.setText(msg)
            self.statusBar.showMessage(msg)
            return

        used = (self.selected_powder.target_gpm / 60.0) * duration

        op, ok = QInputDialog.getText(self, "Complete Process",
                                      f"Duration: {duration:.1f}s\nUsed: {used:.2f}g\n\nOperator Name:",
                                      text=self.config.operator_name)  # <-- Ключевое изменение

        if ok and op:
            if self.net.log_usage(self.selected_powder.name, used, duration, op):
                msg = f"Success! Logged {used:.2f}g by {op}."
                self.lbl_sys_log.setText(msg)
                self.statusBar.showMessage(msg)
                self.check_stock_status()
                logger.info(f"Usage logged: {used}g of {self.selected_powder.name} by {op}")
            else:
                QMessageBox.critical(self, "Error", "Failed to save log to server.")
        else:
            logger.warning("User cancelled logging dialog")

    def update_live_data(self, data: dict):
        self.lbl_rpm_val.setText(f"{data['rpm']:.1f}")
        self.lbl_time_val.setText(f"{data['time']:.1f}s")

        if self.selected_powder:
            mass = (self.selected_powder.target_gpm / 60.0) * data['time']
            self.lbl_mass_val.setText(f"{mass:.2f}g")

        self.data_buffer_x.append(data['time'])
        self.data_buffer_y.append(data['rpm'])

        x_list = list(self.data_buffer_x)
        y_list = list(self.data_buffer_y)

        self.curve.setData(x_list, y_list)

        if len(x_list) > 0:
            current_max_x = x_list[-1]
            window_size = 60
            if current_max_x > window_size:
                self.plot_widget.setXRange(current_max_x - window_size, current_max_x, padding=0.05)
            else:
                self.plot_widget.setXRange(0, max(60, current_max_x + 5), padding=0.05)

    def reset_visuals(self):
        self.lbl_rpm_val.setText("0.0")
        self.lbl_time_val.setText("0.0s")
        self.lbl_mass_val.setText("0.0g")
        self.plot_widget.clear()
        self.curve = self.plot_widget.plot(pen=pg.mkPen('#f1c40f', width=2))
        self.data_buffer_x.clear()
        self.data_buffer_y.clear()
        self.progress_ring.set_value(0.0)

    def background_update(self):
        self.check_stock_status()
        # Не обновляем весь список порошков постоянно, чтобы не мерцало, только если нужно
        # self.refresh_powder_list()

    def show_stats(self):
        dlg = StatsDialog(self.net, self)
        dlg.exec()

    def add_material_dialog(self):
        name, ok = QInputDialog.getText(self, "Add Material", "Name:")
        if not ok or not name: return
        d, ok = QInputDialog.getDouble(self, "Add", "Density:", 1.0)
        if not ok: return
        f, ok = QInputDialog.getDouble(self, "Add", "Flow Factor:", 1.0)
        if not ok: return
        g, ok = QInputDialog.getDouble(self, "Add", "Target GPM:", 10.0)
        if not ok: return

        if self.net.add_powder(name, d, f, g):
            QMessageBox.information(self, "Success", "Material added.")
            self.refresh_powder_list()
            logger.info(f"New material added: {name}")
        else:
            QMessageBox.critical(self, "Error", "Failed to add material.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Глобальная настройка шрифтов для лучшего вида
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = PlasmaClientEnhanced()
    window.show()
    sys.exit(app.exec())