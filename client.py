import sys
import requests
import time
from dataclasses import dataclass
from typing import Optional, List
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
                             QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit,
                             QMessageBox, QHeaderView, QGroupBox, QFormLayout, QInputDialog, QDialog)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QElapsedTimer
from PyQt6.QtGui import QFont, QColor

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


@dataclass
class LogData:
    id: int
    timestamp: str
    powder_name: str
    consumed_grams: float
    operator: str
    duration_sec: float


class NetworkManager:
    @staticmethod
    def get_powders() -> List[PowderData]:
        try:
            resp = requests.get(f"{API_URL}/powders/", timeout=5)
            resp.raise_for_status()
            return [PowderData(**item) for item in resp.json()]
        except Exception as e:
            print(f"Network Error (get_powders): {e}")
            return []

    @staticmethod
    def get_stock() -> List[StockData]:
        try:
            resp = requests.get(f"{API_URL}/inventory/", timeout=5)
            resp.raise_for_status()
            return [StockData(**item) for item in resp.json()]
        except Exception as e:
            print(f"Network Error (get_stock): {e}")
            return []

    @staticmethod
    def get_logs() -> List[LogData]:
        try:
            resp = requests.get(f"{API_URL}/logs/", timeout=5)
            resp.raise_for_status()
            return [LogData(**item) for item in resp.json()]
        except Exception as e:
            print(f"Network Error (get_logs): {e}")
            return []

    @staticmethod
    def log_usage(name: str, grams: float, duration: float, op: str) -> bool:
        try:
            payload = {"powder_name": name, "consumed_grams": grams, "duration_sec": duration, "operator": op}
            resp = requests.post(f"{API_URL}/log_usage/", json=payload, timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Network Error (log_usage): {e}")
            return False

    @staticmethod
    def add_powder(name: str, density: float, factor: float, gpm: float) -> bool:
        try:
            resp = requests.post(f"{API_URL}/powders/",
                                 json={"name": name, "density": density, "flow_factor": factor, "target_gpm": gpm},
                                 timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Network Error (add_powder): {e}")
            return False


class DosageCalculator:
    @staticmethod
    def calculate_rpm(p: PowderData) -> float:
        # Формула: (Target / Density) / Vol * Pitch * Factor * 60
        return round(((p.target_gpm / p.density) / 10.0) * 2.5 * p.flow_factor * 60, 2)

    @staticmethod
    def calculate_mass(gpm: float, duration_sec: float) -> float:
        return round((gpm / 60.0) * duration_sec, 2)


class HardwareSimulator(QThread):
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.running = False
        self.rpm = 0.0
        self._elapsed_timer = QElapsedTimer()
        self._total_seconds = 0.0

    def start_dosage(self, rpm: float):
        self.rpm = rpm
        self.running = True
        self._total_seconds = 0.0
        self._elapsed_timer.start()
        self.start()

    def stop_dosage(self):
        self.running = False
        self.wait(1000)
        if self.isRunning():
            self.terminate()
        self.finished_signal.emit()

    def get_duration(self) -> float:
        return self._total_seconds

    def run(self):
        self._elapsed_timer.start()
        while self.running:
            # Обновляем накопленное время
            self._total_seconds = self._elapsed_timer.elapsed() / 1000.0
            self.status_signal.emit(f"Running: {self.rpm:.2f} RPM | Time: {self._total_seconds:.1f}s")
            self.msleep(500)  # Частота обновления статуса
        self.status_signal.emit("Motor stopping...")


class StatsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Production Statistics & Stock")
        self.setGeometry(100, 100, 800, 500)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        self.stock_table = QTableWidget()
        self.stock_table.setColumnCount(3)
        self.stock_table.setHorizontalHeaderLabels(["Powder", "Current Stock (g)", "Status"])
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.stock_table, "Warehouse Stock")

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(5)
        self.log_table.setHorizontalHeaderLabels(["Time", "Powder", "Used (g)", "Duration (s)", "Operator"])
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.log_table, "Usage History")

        layout.addWidget(tabs)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)
        self.refresh_data()

    def refresh_data(self):
        stock = NetworkManager.get_stock()
        self.stock_table.setRowCount(len(stock))
        for i, s in enumerate(stock):
            self.stock_table.setItem(i, 0, QTableWidgetItem(s.powder_name))
            self.stock_table.setItem(i, 1, QTableWidgetItem(f"{s.quantity_grams:.1f}"))
            status = "OK" if s.quantity_grams > 500 else "LOW STOCK"
            item = QTableWidgetItem(status)
            if s.quantity_grams <= 500: item.setForeground(QColor("red"))
            self.stock_table.setItem(i, 2, item)

        logs = NetworkManager.get_logs()
        self.log_table.setRowCount(len(logs))
        for i, l in enumerate(logs):
            self.log_table.setItem(i, 0, QTableWidgetItem(l.timestamp[:19]))
            self.log_table.setItem(i, 1, QTableWidgetItem(l.powder_name))
            self.log_table.setItem(i, 2, QTableWidgetItem(f"{l.consumed_grams:.2f}"))
            self.log_table.setItem(i, 3, QTableWidgetItem(f"{l.duration_sec:.1f}"))
            self.log_table.setItem(i, 4, QTableWidgetItem(l.operator))


class PlasmaClientUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.hw = HardwareSimulator()
        self.hw.status_signal.connect(self.update_status_log)
        self.hw.finished_signal.connect(self.on_hardware_finished)

        self.selected_powder: Optional[PowderData] = None
        self.current_rpm_value = 0.0

        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_background)
        self.refresh_timer.start(5000)

        self.init_ui()
        self.refresh_table_data()

    def init_ui(self):
        self.setWindowTitle("Plasma Control & Statistics (Stable)")
        self.setGeometry(100, 100, 1000, 650)
        cw = QWidget()
        self.setCentralWidget(cw)
        layout = QHBoxLayout(cw)

        left = QGroupBox("Process Control")
        right = QGroupBox("Info & Actions")
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Name", "Density", "Factor", "Target"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_select)

        lv = QVBoxLayout(left)
        lv.addWidget(self.table)

        form = QFormLayout()
        self.lbl_sel = QLabel("Selected: None")
        self.lbl_sel.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.inp_rpm = QLineEdit()
        self.inp_rpm.setReadOnly(True)
        self.btn_calc = QPushButton("Calculate RPM")
        self.btn_start = QPushButton("START DOSAGE")
        self.btn_stop = QPushButton("STOP & LOG")
        self.btn_stats = QPushButton("View Statistics")
        self.btn_add = QPushButton("Add Powder")

        self.btn_stop.setEnabled(False)

        self.btn_start.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; padding: 10px;")
        self.btn_stop.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 10px;")
        self.btn_stats.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px;")

        form.addRow("Status:", self.lbl_sel)
        form.addRow("RPM:", self.inp_rpm)
        form.addRow(self.btn_calc)
        form.addRow(self.btn_start)
        form.addRow(self.btn_stop)
        form.addRow(self.btn_stats)
        form.addRow(self.btn_add)

        self.log_label = QLabel("Ready")
        self.log_label.setStyleSheet(
            "border: 1px solid #ccc; padding: 5px; background: #f9f9f9; word-wrap: break-word;")
        self.log_label.setWordWrap(True)
        form.addRow("System Log:", self.log_label)

        rv = QVBoxLayout(right)
        rv.addLayout(form)
        rv.addStretch()
        layout.addWidget(left, 2)
        layout.addWidget(right, 1)

        self.btn_calc.clicked.connect(self.calc)
        self.btn_start.clicked.connect(self.start_process)
        self.btn_stop.clicked.connect(self.stop_and_log)
        self.btn_stats.clicked.connect(self.show_stats)
        self.btn_add.clicked.connect(self.add_new)

    def update_status_log(self, msg: str):
        self.log_label.setText(msg)

    def on_hardware_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.log_label.setText("Process stopped safely.")

    def refresh_background(self):
        # Тихое обновление данных фона, если нужно
        pass

    def on_select(self):
        items = self.table.selectedItems()
        if not items: return
        try:
            name = self.table.item(items[0].row(), 1).text()
            powders = NetworkManager.get_powders()
            self.selected_powder = next((p for p in powders if p.name == name), None)
            if self.selected_powder:
                self.lbl_sel.setText(f"Selected: {self.selected_powder.name}")
                self.inp_rpm.clear()
                self.current_rpm_value = 0.0
        except Exception as e:
            self.log_label.setText(f"Selection error: {e}")

    def calc(self):
        if not self.selected_powder:
            QMessageBox.warning(self, "Error", "Select powder first")
            return
        try:
            rpm = DosageCalculator.calculate_rpm(self.selected_powder)
            self.current_rpm_value = rpm
            self.inp_rpm.setText(str(rpm))
            self.log_label.setText(f"Calculated: {rpm} RPM")
        except Exception as e:
            self.log_label.setText(f"Calculation error: {e}")

    def start_process(self):
        if not self.selected_powder or self.inp_rpm.text() == "":
            QMessageBox.warning(self, "Error", "Select powder and calculate RPM first")
            return

        try:
            # Проверка склада
            stock = NetworkManager.get_stock()
            current_stock = next((s.quantity_grams for s in stock if s.powder_name == self.selected_powder.name), 0)

            if current_stock < 100:
                reply = QMessageBox.warning(self, "Low Stock", f"Only {current_stock:.1f}g left.\nStart anyway?",
                                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return

            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.hw.start_dosage(self.current_rpm_value)
            self.log_label.setText("Dosage started...")

        except Exception as e:
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            QMessageBox.critical(self, "Critical Error", f"Failed to start: {e}")

    def stop_and_log(self):
        self.hw.stop_dosage()
        if not self.selected_powder or self.current_rpm_value == 0:
            return

        # Получаем реальное время работы из потока
        duration = self.hw.get_duration()

        if duration < 1.0:
            QMessageBox.information(self, "Info", "Process duration too short (<1s). No material logged.")
            self.on_hardware_finished()
            return

        try:
            gpm = self.selected_powder.target_gpm
            used = DosageCalculator.calculate_mass(gpm, duration)

            op_name, ok = QInputDialog.getText(self, "Operator ID",
                                               f"Process finished.\nDuration: {duration:.1f}s\nUsed: {used:.2f}g\n\nEnter Operator Name:")
            if not ok or not op_name:
                op_name = "Unknown"

            self.log_label.setText("Saving usage log to server...")
            QApplication.processEvents()  # Обновить UI пока ждем сеть

            if NetworkManager.log_usage(self.selected_powder.name, used, duration, op_name):
                QMessageBox.information(self, "Success",
                                        f"Process finished.\nUsed: {used:.2f}g\nDuration: {duration:.1f}s\nStock updated.")
                self.refresh_table_data()  # Обновить таблицу порошков (если бы там был остаток)
            else:
                QMessageBox.critical(self, "Error",
                                     "Failed to save log. Check server connection or stock levels.\nData might be lost.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Logging failed: {e}")
        finally:
            if self.btn_stop.isEnabled():
                self.on_hardware_finished()

    def show_stats(self):
        dialog = StatsDialog(self)
        dialog.exec()

    def add_new(self):
        name, ok = QInputDialog.getText(self, "Add", "Powder Name:")
        if not ok or not name: return
        dens, ok = QInputDialog.getDouble(self, "Add", "Density:", 1.0, 0.1, 100.0, 2)
        if not ok: return
        fact, ok = QInputDialog.getDouble(self, "Add", "Flow Factor:", 1.0, 0.1, 10.0, 2)
        if not ok: return
        gpm, ok = QInputDialog.getDouble(self, "Add", "Target GPM:", 10.0, 0.1, 1000.0, 1)
        if not ok: return

        if NetworkManager.add_powder(name, dens, fact, gpm):
            self.refresh_table_data()
            QMessageBox.information(self, "Success", "Added with 5kg initial stock")
        else:
            QMessageBox.critical(self, "Error", "Failed to add (Name exists or Network error)")

    def refresh_table_data(self):
        data = NetworkManager.get_powders()
        self.table.setRowCount(len(data))
        for i, p in enumerate(data):
            self.table.setItem(i, 0, QTableWidgetItem(str(p.id)))
            self.table.setItem(i, 1, QTableWidgetItem(p.name))
            self.table.setItem(i, 2, QTableWidgetItem(f"{p.density:.2f}"))
            self.table.setItem(i, 3, QTableWidgetItem(f"{p.flow_factor:.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(f"{p.target_gpm:.1f}"))


if __name__ == "__main__":

    import traceback


    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        print("Uncaught exception", "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))


    sys.excepthook = handle_exception

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = PlasmaClientUI()
    win.show()
    sys.exit(app.exec())