import sys
import requests
import time
from dataclasses import dataclass
from typing import Optional, List, Deque
from collections import deque
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit,
                             QMessageBox, QHeaderView, QGroupBox, QFormLayout, QInputDialog,
                             QDialog, QFrame, QScrollArea, QTabWidget, QGridLayout)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QElapsedTimer
from PyQt6.QtGui import QFont, QColor, QPainter, QPen
import pyqtgraph as pg

STYLESHEET = """
QPushButton:hover { background-color: #465f75; }
QPushButton:pressed { background-color: #2c3e50; }
QPushButton:disabled { background-color: #2c3e50; color: #7f8c8d; }
QPushButton#btnStart { background-color: #27ae60; }
QPushButton#btnStart:hover { background-color: #2ecc71; }
QPushButton#btnStop { background-color: #c0392b; }
QPushButton#btnStop:hover { background-color: #e74c3c; }
QPushButton#btnStats { background-color: #2980b9; }
QLabel { color: #ecf0f1; }
QLabel#bigValue { font-size: 24px; font-weight: bold; color: #f1c40f; }
QLabel#statusOk { color: #2ecc71; font-weight: bold; }
QLabel#statusLow { color: #e74c3c; font-weight: bold; }
"""

API_URL = "http://127.0.0.1:8000"


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


class NetworkManager:
    @staticmethod
    def get_powders() -> List[PowderData]:
        try:
            resp = requests.get(f"{API_URL}/powders/", timeout=5)
            return [PowderData(**item) for item in resp.json()]
        except:
            return []

    @staticmethod
    def get_stock() -> List[StockData]:
        try:
            resp = requests.get(f"{API_URL}/inventory/", timeout=5)
            return [StockData(**item) for item in resp.json()]
        except:
            return []

    @staticmethod
    def get_logs(limit=50):
        try:
            resp = requests.get(f"{API_URL}/logs/", params={"limit": limit}, timeout=5)
            return resp.json()
        except:
            return []

    @staticmethod
    def log_usage(name: str, grams: float, duration: float, op: str) -> bool:
        try:
            resp = requests.post(f"{API_URL}/log_usage/",
                                 json={"powder_name": name, "consumed_grams": grams, "duration_sec": duration,
                                       "operator": op}, timeout=5)
            return resp.status_code == 200
        except:
            return False

    @staticmethod
    def add_powder(name: str, density: float, factor: float, gpm: float) -> bool:
        try:
            resp = requests.post(f"{API_URL}/powders/",
                                 json={"name": name, "density": density, "flow_factor": factor, "target_gpm": gpm},
                                 timeout=5)
            return resp.status_code == 200
        except:
            return False


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

    def stop_dosage(self):
        self.running = False
        self.wait(1000)
        if self.isRunning(): self.terminate()
        self.finished_signal.emit(self._current_time)

    def run(self):
        while self.running:
            self._current_time = self._timer.elapsed() / 1000.0
            # Эмуляция небольших колебаний RPM (шум ±0.5)
            noise = (hash(str(time.time())) % 100) / 100.0 - 0.5
            current_rpm = self.rpm + noise

            data = {
                "time": self._current_time,
                "rpm": current_rpm,
                "status": "running"
            }
            self.status_signal.emit(data)
            self.msleep(200)  # 5 обновлений в секунду

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


class StatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
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
        table.setStyleSheet("background-color: #2c3e50; color: white;")
        self.stock_table_ref = table
        return table

    def create_log_widget(self):
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Time", "Powder", "Used (g)", "Duration", "Operator"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setStyleSheet("background-color: #2c3e50; color: white;")
        self.log_table_ref = table
        return table

    def refresh_data(self):
        stock = NetworkManager.get_stock()
        t = self.stock_table_ref
        t.setRowCount(len(stock))
        for i, s in enumerate(stock):
            t.setItem(i, 0, QTableWidgetItem(s.powder_name))
            t.setItem(i, 1, QTableWidgetItem(f"{s.quantity_grams:.1f}"))
            status = "OK" if s.quantity_grams > 500 else "LOW"
            item = QTableWidgetItem(status)
            item.setForeground(QColor("#2ecc71" if s.quantity_grams > 500 else "#e74c3c"))
            t.setItem(i, 2, item)

        logs = NetworkManager.get_logs()
        t = self.log_table_ref
        t.setRowCount(len(logs))
        for i, l in enumerate(logs):
            t.setItem(i, 0, QTableWidgetItem(l['timestamp'][:19]))
            t.setItem(i, 1, QTableWidgetItem(l['powder_name']))
            # Логика отображения: приход (+), расход (-)
            # В БД расход хранится как положительное consumed_grams
            change = -l['consumed_grams']
            item_val = QTableWidgetItem(f"{change:.2f}")
            item_val.setForeground(QColor("#2ecc71" if change > 0 else "#e74c3c"))
            t.setItem(i, 2, item_val)
            t.setItem(i, 3, QTableWidgetItem(f"{l['duration_sec']:.1f}s"))
            t.setItem(i, 4, QTableWidgetItem(l['operator']))


class PlasmaClientEnhanced(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoPlasma Client")
        self.setStyleSheet(STYLESHEET)
        self.setGeometry(100, 100, 1200, 800)

        self.hw = HardwareSimulator()
        self.hw.status_signal.connect(self.update_live_data)
        self.hw.finished_signal.connect(self.on_process_finished)

        self.selected_powder: Optional[PowderData] = None
        self.current_rpm = 0.0

        self.data_buffer_x: Deque[float] = deque(maxlen=200)
        self.data_buffer_y: Deque[float] = deque(maxlen=200)

        self.init_ui()
        self.refresh_powder_list()

        self.timer = QTimer()
        self.timer.timeout.connect(self.background_update)
        self.timer.start(5000)

    def init_ui(self):
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
        self.lbl_sys_log.setStyleSheet("color: #95a5a6; border: 1px solid #34495e; padding: 10px; border-radius: 4px;")
        self.lbl_sys_log.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Selection:", self.lbl_sel_info)
        form.addRow("Availability:", self.lbl_stock_info)

        right_layout.addLayout(form)
        right_layout.addWidget(self.lbl_sys_log)
        right_layout.addStretch()

        main_layout.addWidget(left_panel, 2)
        main_layout.addWidget(center_panel, 2)
        main_layout.addWidget(right_panel, 1)

    def refresh_powder_list(self):
        data = NetworkManager.get_powders()
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
        powders = NetworkManager.get_powders()
        self.selected_powder = next((p for p in powders if p.name == name), None)

        if self.selected_powder:
            self.lbl_sel_info.setText(self.selected_powder.name)
            self.check_stock_status()
            self.btn_calc.setEnabled(True)
            self.reset_visuals()

    def check_stock_status(self):
        if not self.selected_powder: return
        stock = NetworkManager.get_stock()
        current = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)

        self.lbl_stock_info.setText(f"Stock: {current:.1f} g")
        if current < 500:
            self.lbl_stock_info.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self.lbl_stock_info.setStyleSheet("color: #2ecc71; font-weight: bold;")

    def calculate_params(self):
        if not self.selected_powder: return
        rpm = round(((
                                 self.selected_powder.target_gpm / self.selected_powder.density) / 10.0) * 2.5 * self.selected_powder.flow_factor * 60,
                    2)
        self.current_rpm = rpm
        self.lbl_sys_log.setText(f"Parameters calculated: {rpm} RPM")
        self.btn_start.setEnabled(True)
        self.reset_visuals()

        max_expected = rpm * 1.2
        # self.plot_widget.setYRange(0, max(10, max_expected))

    def start_process(self):
        if not self.selected_powder or self.current_rpm == 0: return

        stock = NetworkManager.get_stock()
        current = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)
        if current < 50:
            if QMessageBox.warning(self, "Critical Low Stock", "Stock critically low. Start anyway?",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_calc.setEnabled(False)
        self.lbl_sys_log.setText("Process STARTED.")
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
            self.lbl_sys_log.setText("Stopped (too short).")
            return

        used = (self.selected_powder.target_gpm / 60.0) * duration

        op, ok = QInputDialog.getText(self, "Complete Process",
                                      f"Duration: {duration:.1f}s\nUsed: {used:.2f}g\n\nOperator Name:")
        if ok and op:
            if NetworkManager.log_usage(self.selected_powder.name, used, duration, op):
                self.lbl_sys_log.setText(f"Success! Logged {used:.2f}g.")
                self.check_stock_status()
            else:
                QMessageBox.critical(self, "Error", "Failed to save log.")

    def update_live_data(self, dict):
        self.lbl_rpm_val.setText(f"{dict['rpm']:.1f}")
        self.lbl_time_val.setText(f"{dict['time']:.1f}s")

        if self.selected_powder:
            mass = (self.selected_powder.target_gpm / 60.0) * dict['time']
            self.lbl_mass_val.setText(f"{mass:.2f}g")

        self.data_buffer_x.append(dict['time'])
        self.data_buffer_y.append(dict['rpm'])

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

            # view_range = self.plot_widget.getViewBox().state['viewRange']
            # y_min, y_max = view_range[1]
            # if dict['rpm'] > y_max * 0.9 or dict['rpm'] < y_min:
            #     new_max = max(dict['rpm'] * 1.2, 10)
            #     self.plot_widget.setYRange(0, new_max, padding=0.1)

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
        self.refresh_powder_list()

    def show_stats(self):
        dlg = StatsDialog(self)
        dlg.exec()
        dlg.refresh_data()

    def add_material_dialog(self):
        name, ok = QInputDialog.getText(self, "Add Material", "Name:")
        if not ok: return
        d, ok = QInputDialog.getDouble(self, "Add", "Density:", 1.0)
        if not ok: return
        f, ok = QInputDialog.getDouble(self, "Add", "Flow Factor:", 1.0)
        if not ok: return
        g, ok = QInputDialog.getDouble(self, "Add", "Target GPM:", 10.0)
        if not ok: return

        if NetworkManager.add_powder(name, d, f, g):
            QMessageBox.information(self, "Success", "Material added.")
            self.refresh_powder_list()
        else:
            QMessageBox.critical(self, "Error", "Failed to add.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = PlasmaClientEnhanced()
    window.show()
    sys.exit(app.exec())