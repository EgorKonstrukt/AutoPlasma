import sys
import os
import json
import requests
import time
import logging
import platform
from dataclasses import dataclass, asdict
from typing import Optional, List, Deque
from collections import deque
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit,
                             QMessageBox, QHeaderView, QGroupBox, QFormLayout, QInputDialog,
                             QDialog, QTabWidget, QGridLayout, QMenuBar, QMenu, QStatusBar,
                             QSpinBox, QDoubleSpinBox, QCheckBox, QDialogButtonBox, QAction,
                             QComboBox, QShortcut)
from PyQt5.QtCore import Qt, QTimer, QSettings
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QKeySequence

import pyqtgraph as pg

from src.modbus.modbus_worker import ModbusWorker
from src.modbus.modbus_feeder import FeederStatus

CONFIG_FILE = "client_config.json"
LOG_FILE = "client.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger("AutoPlasmaClient")


@dataclass
class ClientConfig:
    operator_name: str = "Оператор"
    window_geometry: Optional[List[int]] = None
    graph_history_size: int = 200
    api_url: str = "http://127.0.0.1:8000"
    modbus_port: str = "COM3" if platform.system() == "Windows" else "/dev/ttyUSB0"
    modbus_baudrate: int = 115200
    modbus_slave_id: int = 1
    use_modbus: bool = True

    @staticmethod
    def load() -> 'ClientConfig':
        if not os.path.exists(CONFIG_FILE):
            return ClientConfig()
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return ClientConfig(**{k: v for k, v in data.items() if k in ClientConfig.__annotations__})
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return ClientConfig()

    def save(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(asdict(self), f, indent=4)
        except Exception as e:
            logger.error(f"Ошибка сохранения конфигурации: {e}")


class NetworkManager:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.connected = False

    def check_connection(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/powders/", timeout=2)
            resp.raise_for_status()
            self.connected = True
            return True
        except Exception:
            self.connected = False
            return False

    def get_powders(self) -> List['PowderData']:
        try:
            resp = requests.get(f"{self.base_url}/powders/", timeout=5)
            resp.raise_for_status()
            self.connected = True
            return [PowderData(**item) for item in resp.json()]
        except Exception as e:
            self.connected = False
            logger.error(f"Сетевая ошибка (get_powders): {e}")
            return []

    def get_stock(self) -> List['StockData']:
        try:
            resp = requests.get(f"{self.base_url}/inventory/", timeout=5)
            resp.raise_for_status()
            return [StockData(**item) for item in resp.json()]
        except Exception as e:
            logger.error(f"Сетевая ошибка (get_stock): {e}")
            return []

    def get_logs(self, limit=50):
        try:
            resp = requests.get(f"{self.base_url}/logs/", params={"limit": limit}, timeout=5)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Сетевая ошибка (get_logs): {e}")
            return []

    def log_usage(self, name: str, grams: float, duration: float, op: str) -> bool:
        try:
            resp = requests.post(f"{self.base_url}/log_usage/",
                                 json={"powder_name": name, "consumed_grams": grams, "duration_sec": duration,
                                       "operator": op}, timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Сетевая ошибка (log_usage): {e}")
            return False

    def add_powder(self, name: str, density: float, factor: float, gpm: float) -> bool:
        try:
            resp = requests.post(f"{self.base_url}/powders/",
                                 json={"name": name, "density": density, "flow_factor": factor, "target_gpm": gpm},
                                 timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Сетевая ошибка (add_powder): {e}")
            return False


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
        painter.setPen(QPen(QColor("#34495e"), 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)
        if self.value > 0:
            painter.setPen(QPen(self.color, 15, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            span = int(self.value * 360 * 16)
            painter.drawArc(rect, -90 * 16, span)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{int(self.value * 100)}%")


class SettingsDialog(QDialog):
    def __init__(self, config: ClientConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Настройки")
        self.setModal(True)
        self.setGeometry(100, 100, 450, 400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.op_input = QLineEdit(self.config.operator_name)
        form.addRow("Оператор:", self.op_input)

        self.api_input = QLineEdit(self.config.api_url)
        form.addRow("API URL:", self.api_input)

        self.graph_size = QSpinBox()
        self.graph_size.setRange(50, 1000)
        self.graph_size.setValue(self.config.graph_history_size)
        form.addRow("Точек истории:", self.graph_size)

        form.addRow(QLabel("<b>Настройки Modbus</b>"))
        self.modbus_enabled = QCheckBox("Использовать Modbus")
        self.modbus_enabled.setChecked(self.config.use_modbus)
        form.addRow("", self.modbus_enabled)

        self.modbus_port = QLineEdit(self.config.modbus_port)
        form.addRow("Порт:", self.modbus_port)

        self.modbus_baud = QComboBox()
        baudrates = ["9600", "19200", "38400", "57600", "115200"]
        self.modbus_baud.addItems(baudrates)
        self.modbus_baud.setCurrentText(str(self.config.modbus_baudrate))
        form.addRow("Битрейт:", self.modbus_baud)

        self.modbus_slave = QSpinBox()
        self.modbus_slave.setRange(1, 247)
        self.modbus_slave.setValue(self.config.modbus_slave_id)
        form.addRow("Slave ID:", self.modbus_slave)

        layout.addLayout(form)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.save_settings)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def save_settings(self):
        self.config.operator_name = self.op_input.text().strip() or "Оператор"
        self.config.api_url = self.api_input.text().strip() or "http://127.0.0.1:8000"
        self.config.graph_history_size = self.graph_size.value()
        self.config.use_modbus = self.modbus_enabled.isChecked()
        self.config.modbus_port = self.modbus_port.text().strip() or "COM3"
        self.config.modbus_baudrate = int(self.modbus_baud.currentText())
        self.config.modbus_slave_id = self.modbus_slave.value()
        self.config.save()
        self.accept()


class StatsDialog(QDialog):
    def __init__(self, net_manager: NetworkManager, parent=None):
        super().__init__(parent)
        self.net = net_manager
        self.setWindowTitle("Статистика")
        self.setGeometry(100, 100, 900, 600)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self.create_stock_widget(), "Склад")
        tabs.addTab(self.create_log_widget(), "История")
        layout.addWidget(tabs)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)
        self.refresh_data()

    def create_stock_widget(self):
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Порошок", "В наличии (г)", "Статус"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stock_table_ref = table
        return table

    def create_log_widget(self):
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Время", "Порошок", "Использовано (г)", "Длительность", "Оператор"])
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
            status = "OK" if s.quantity_grams > 500 else "МАЛО"
            item = QTableWidgetItem(status)
            item.setForeground(QColor("#2ecc71" if s.quantity_grams > 500 else "#e74c3c"))
            t.setItem(i, 2, item)

        logs = self.net.get_logs()
        t = self.log_table_ref
        t.setRowCount(len(logs))
        for i, l in enumerate(logs):
            t.setItem(i, 0, QTableWidgetItem(l['timestamp'][:19]))
            t.setItem(i, 1, QTableWidgetItem(l['powder_name']))
            t.setItem(i, 2, QTableWidgetItem(f"-{l['consumed_grams']:.2f}"))
            t.setItem(i, 3, QTableWidgetItem(f"{l['duration_sec']:.1f}s"))
            t.setItem(i, 4, QTableWidgetItem(l['operator']))


class PlasmaClient(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = ClientConfig.load()
        self.net = NetworkManager(self.config.api_url)

        self.setWindowTitle(f"AutoPlasma - {self.config.operator_name}")
        if self.config.window_geometry:
            self.setGeometry(*self.config.window_geometry)
        else:
            self.setGeometry(100, 100, 1200, 800)

        self.modbus_worker: Optional[ModbusWorker] = None
        self.modbus_connected = False
        self.current_feeder_status: Optional[FeederStatus] = None

        self.selected_powder: Optional[PowderData] = None
        self.current_rpm = 0.0
        self.process_start_time = 0.0

        self.data_buffer_x: Deque[float] = deque(maxlen=self.config.graph_history_size)
        self.data_buffer_y: Deque[float] = deque(maxlen=self.config.graph_history_size)

        self.init_ui()
        self.refresh_powder_list()
        self.init_modbus()
        self.check_api_connection()

        self.timer = QTimer()
        self.timer.timeout.connect(self.background_update)
        self.timer.start(5000)

        # F5 Reconnect Shortcut
        self.reconnect_shortcut = QShortcut(QKeySequence("F5"), self)
        self.reconnect_shortcut.activated.connect(self.reconnect_api)

        logger.info("Клиент инициализирован.")

    def init_ui(self):
        self._create_menu_bar()
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        left_panel = QGroupBox("Материал")
        left_layout = QVBoxLayout(left_panel)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Название", "Плотность", "Коэфф.", "GPM"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_selection_change)
        left_layout.addWidget(self.table)
        btn_add = QPushButton("Добавить")
        btn_add.clicked.connect(self.add_material_dialog)
        left_layout.addWidget(btn_add)

        center_panel = QGroupBox("Процесс")
        center_layout = QVBoxLayout(center_panel)

        self.api_status_label = QLabel("API: Проверка...")
        self.api_status_label.setStyleSheet("color: #f39c12; font-weight: bold;")
        center_layout.addWidget(self.api_status_label)

        self.modbus_status_label = QLabel("Modbus: Отключено")
        self.modbus_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        center_layout.addWidget(self.modbus_status_label)

        top_indicators = QHBoxLayout()
        self.progress_ring = CircularProgress()
        top_indicators.addWidget(self.progress_ring)

        readouts = QGridLayout()
        self.lbl_rpm_val = QLabel("0.0")
        self.lbl_time_val = QLabel("0.0s")
        self.lbl_mass_val = QLabel("0.0g")
        for lbl in [self.lbl_rpm_val, self.lbl_time_val, self.lbl_mass_val]:
            lbl.setFont(QFont("Arial", 12, QFont.Weight.Bold))

        readouts.addWidget(QLabel("RPM:"), 0, 0)
        readouts.addWidget(self.lbl_rpm_val, 0, 1)
        readouts.addWidget(QLabel("Время:"), 1, 0)
        readouts.addWidget(self.lbl_time_val, 1, 1)
        readouts.addWidget(QLabel("Масса:"), 2, 0)
        readouts.addWidget(self.lbl_mass_val, 2, 1)
        top_indicators.addLayout(readouts, 1)
        center_layout.addLayout(top_indicators)

        self.plot_widget = pg.PlotWidget(background='#2c3e50')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('left', 'RPM')
        self.plot_widget.setLabel('bottom', 'Время (с)')
        self.curve = self.plot_widget.plot(pen=pg.mkPen('#f1c40f', width=2))
        center_layout.addWidget(self.plot_widget)

        ctrl_layout = QHBoxLayout()
        self.btn_calc = QPushButton("Расчет")
        self.btn_start = QPushButton("СТАРТ")
        self.btn_start.setStyleSheet("background-color: #2ecc71; color: white;")
        self.btn_stop = QPushButton("СТОП")
        self.btn_stop.setStyleSheet("background-color: #e74c3c; color: white;")
        self.btn_stats = QPushButton("Отчет")
        self.btn_reset = QPushButton("Сброс")
        self.btn_reconnect = QPushButton("Переподключить API (F5)")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_reset.setEnabled(False)

        self.btn_calc.clicked.connect(self.calculate_params)
        self.btn_start.clicked.connect(self.start_process)
        self.btn_stop.clicked.connect(self.stop_process)
        self.btn_stats.clicked.connect(self.show_stats)
        self.btn_reset.clicked.connect(self.reset_feeder)
        self.btn_reconnect.clicked.connect(self.reconnect_api)

        ctrl_layout.addWidget(self.btn_calc)
        ctrl_layout.addWidget(self.btn_start)
        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addWidget(self.btn_reset)
        ctrl_layout.addWidget(self.btn_stats)
        ctrl_layout.addWidget(self.btn_reconnect)
        center_layout.addLayout(ctrl_layout)

        right_panel = QGroupBox("Состояние")
        right_layout = QVBoxLayout(right_panel)
        self.lbl_sel_info = QLabel("Не выбрано")
        self.lbl_stock_info = QLabel("Запас: --")
        self.lbl_feeder_status = QLabel("Дозатор: --")

        self.lbl_sys_log = QLabel("Готов")
        self.lbl_sys_log.setWordWrap(True)
        self.lbl_sys_log.setMinimumHeight(100)
        self.lbl_sys_log.setStyleSheet("border: 1px solid #ccc; padding: 5px;")

        form = QFormLayout()
        form.addRow("Материал:", self.lbl_sel_info)
        form.addRow("Запас:", self.lbl_stock_info)
        form.addRow("Дозатор:", self.lbl_feeder_status)
        right_layout.addLayout(form)
        right_layout.addWidget(QLabel("Журнал:"))
        right_layout.addWidget(self.lbl_sys_log)
        right_layout.addStretch()

        main_layout.addWidget(left_panel, 4)
        main_layout.addWidget(center_panel, 2)
        main_layout.addWidget(right_panel, 3)

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage(f"API: {self.config.api_url}")

    def _create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("Файл")
        act_settings = QAction("Настройки", self)
        act_settings.setShortcut(QKeySequence.StandardKey.Preferences)
        act_settings.triggered.connect(self.open_settings)
        file_menu.addAction(act_settings)
        act_exit = QAction("Выход", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

    def init_modbus(self):
        if self.config.use_modbus:
            self.modbus_worker = ModbusWorker(
                self.config.modbus_port,
                self.config.modbus_baudrate,
                self.config.modbus_slave_id
            )
            self.modbus_worker.status_signal.connect(self.on_modbus_status)
            self.modbus_worker.connected_signal.connect(self.on_modbus_connected)
            self.modbus_worker.error_signal.connect(self.on_modbus_error)
            self.modbus_worker.start()

    def check_api_connection(self):
        if self.net.check_connection():
            self.api_status_label.setText("API: Подключено")
            self.api_status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        else:
            self.api_status_label.setText("API: Ошибка")
            self.api_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")

    def reconnect_api(self):
        self.api_status_label.setText("API: Подключение...")
        self.net.base_url = self.config.api_url
        if self.net.check_connection():
            self.api_status_label.setText("API: Подключено")
            self.api_status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self.statusBar.showMessage("API переподключено", 3000)
            self.refresh_powder_list()
            self.check_stock_status()
        else:
            self.api_status_label.setText("API: Ошибка")
            self.api_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            QMessageBox.warning(self, "Ошибка", "Не удалось подключиться к серверу базы данных")

    def open_settings(self):
        if self.modbus_worker and self.modbus_worker.isRunning():
            reply = QMessageBox.question(self, "Внимание",
                                         "Изменение настроек Modbus требует перезапуска. Продолжить?",
                                         QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.net.base_url = self.config.api_url
            self.setWindowTitle(f"AutoPlasma - {self.config.operator_name}")
            if self.modbus_worker:
                self.modbus_worker.stop()
                self.modbus_worker = None
            self.init_modbus()
            self.reconnect_api()

    def closeEvent(self, event):
        self.config.window_geometry = [self.x(), self.y(), self.width(), self.height()]
        self.config.save()
        if self.modbus_worker:
            self.modbus_worker.stop()
            self.modbus_worker.wait(2000)
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
        if not items:
            return
        name = self.table.item(items[0].row(), 1).text()
        powders = self.net.get_powders()
        self.selected_powder = next((p for p in powders if p.name == name), None)
        if self.selected_powder:
            self.lbl_sel_info.setText(self.selected_powder.name)
            self.check_stock_status()
            self.btn_calc.setEnabled(True)
            self.reset_visuals()

    def check_stock_status(self):
        if not self.selected_powder:
            return
        stock = self.net.get_stock()
        current = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)
        self.lbl_stock_info.setText(f"Доступно: {current:.1f} г")
        self.lbl_stock_info.setStyleSheet(
            "color: #e74c3c; font-weight: bold;" if current < 500 else "color: #2ecc71; font-weight: bold;")

    def calculate_params(self):
        if not self.selected_powder:
            return
        rpm = round(((self.selected_powder.target_gpm / self.selected_powder.density) / 10.0) *
                    2.5 * self.selected_powder.flow_factor * 60, 2)
        self.current_rpm = rpm
        self.lbl_sys_log.setText(f"Расчет: {rpm} RPM")
        self.btn_start.setEnabled(True)
        self.reset_visuals()

    def start_process(self):
        if not self.selected_powder or self.current_rpm == 0:
            return
        if self.modbus_connected:
            if self.current_feeder_status and self.current_feeder_status.alarm:
                QMessageBox.critical(self, "Ошибка", "Авария дозатора. Выполните сброс.")
                return
            if not self.modbus_worker.set_speed(self.current_rpm):
                QMessageBox.critical(self, "Ошибка", "Не удалось установить скорость")
                return
            if not self.modbus_worker.start_feeder():
                QMessageBox.critical(self, "Ошибка", "Не удалось запустить дозатор")
                return

        stock = self.net.get_stock()
        current = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)
        if current < 50:
            if QMessageBox.warning(self, "Мало запасов", "Запасы <50 г. Продолжить?",
                                   QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_reset.setEnabled(True)
        self.btn_calc.setEnabled(False)
        self.lbl_sys_log.setText("Процесс запущен")
        self.progress_ring.set_value(0.8, "#2ecc71")
        self.process_start_time = time.time()
        self.data_buffer_x.clear()
        self.data_buffer_y.clear()

    def stop_process(self):
        if self.modbus_connected and self.modbus_worker:
            self.modbus_worker.stop_feeder()
        self.btn_stop.setEnabled(False)
        self.lbl_sys_log.setText("Остановка...")

    def reset_feeder(self):
        if self.modbus_connected and self.modbus_worker:
            if self.modbus_worker.reset_feeder():
                self.lbl_sys_log.setText("Сброс выполнен")
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось сбросить дозатор")
        else:
            QMessageBox.information(self, "Инфо", "Дозатор не подключен")

    def on_modbus_status(self, status: FeederStatus):
        self.current_feeder_status = status
        self.lbl_rpm_val.setText(f"{status.rpm:.1f}")
        status_text = f"Готов: {'✓' if status.ready else '✗'} | Работа: {'✓' if status.running else '✗'} | Авария: {'✗' if status.alarm else '✓'}"
        self.lbl_feeder_status.setText(status_text)
        self.lbl_feeder_status.setStyleSheet(
            "color: #e74c3c; font-weight: bold;" if status.alarm else
            "color: #2ecc71; font-weight: bold;" if status.running else
            "color: #f1c40f; font-weight: bold;"
        )

        elapsed = time.time() - self.process_start_time if self.process_start_time > 0 else 0
        self.lbl_time_val.setText(f"{elapsed:.1f}s")
        if self.selected_powder:
            mass = (self.selected_powder.target_gpm / 60.0) * elapsed
            self.lbl_mass_val.setText(f"{mass:.2f}g")

        self.data_buffer_x.append(elapsed)
        self.data_buffer_y.append(status.rpm)
        self.curve.setData(list(self.data_buffer_x), list(self.data_buffer_y))

    def on_modbus_connected(self, connected: bool):
        self.modbus_connected = connected
        if connected:
            self.modbus_status_label.setText("Modbus: Подключено")
            self.modbus_status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self.btn_reset.setEnabled(True)
        else:
            self.modbus_status_label.setText("Modbus: Отключено")
            self.modbus_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.btn_reset.setEnabled(False)

    def on_modbus_error(self, error: str):
        self.lbl_sys_log.setText(f"Ошибка Modbus: {error}")

    def reset_visuals(self):
        self.lbl_rpm_val.setText("0.0")
        self.lbl_time_val.setText("0.0с")
        self.lbl_mass_val.setText("0.0г")
        self.plot_widget.clear()
        self.curve = self.plot_widget.plot(pen=pg.mkPen('#f1c40f', width=2))
        self.data_buffer_x.clear()
        self.data_buffer_y.clear()
        self.progress_ring.set_value(0.0)
        self.process_start_time = 0.0

    def background_update(self):
        self.check_stock_status()
        if not self.net.connected:
            self.check_api_connection()

    def show_stats(self):
        dlg = StatsDialog(self.net, self)
        dlg.exec()

    def add_material_dialog(self):
        name, ok = QInputDialog.getText(self, "Материал", "Название:")
        if not ok or not name: return
        d, ok = QInputDialog.getDouble(self, "Параметры", "Плотность:", 1.0)
        if not ok: return
        f, ok = QInputDialog.getDouble(self, "Параметры", "Коэффициент:", 1.0)
        if not ok: return
        g, ok = QInputDialog.getDouble(self, "Параметры", "GPM:", 10.0)
        if not ok: return
        if self.net.add_powder(name, d, f, g):
            QMessageBox.information(self, "Успех", "Материал добавлен")
            self.refresh_powder_list()
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось добавить")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    window = PlasmaClient()
    window.show()
    sys.exit(app.exec())