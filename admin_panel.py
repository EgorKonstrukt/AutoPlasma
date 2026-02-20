import sys
import requests
from dataclasses import dataclass
from typing import List, Optional
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
                             QTableWidget, QTableWidgetItem, QPushButton, QLabel, QLineEdit,
                             QMessageBox, QHeaderView, QGroupBox, QFormLayout, QInputDialog,
                             QComboBox, QDialog, QDialogButtonBox, QDateEdit)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush

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
class LogEntry:
    id: int
    timestamp: str
    powder_name: str
    change: float
    operator: str
    comment: str = ""


class AdminNetwork:
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
    def get_logs() -> List[LogEntry]:
        try:
            resp = requests.get(f"{API_URL}/logs/", timeout=5)
            # Преобразуем: если consumed_grams < 0, значит это приход (положительное изменение)
            logs = []
            for item in resp.json():
                change = -item['consumed_grams'] if item['consumed_grams'] < 0 else -item[
                    'consumed_grams']
                real_change = -item['consumed_grams']
                logs.append(LogEntry(
                    id=item['id'], timestamp=item['timestamp'], powder_name=item['powder_name'],
                    change=real_change, operator=item['operator'], comment=""
                ))
            return logs
        except:
            return []

    @staticmethod
    def add_powder(name: str, density: float, factor: float, gpm: float) -> bool:
        try:
            resp = requests.post(f"{API_URL}/powders/",
                                 json={"name": name, "density": density, "flow_factor": factor, "target_gpm": gpm},
                                 timeout=5)
            return resp.status_code == 200
        except:
            return False

    @staticmethod
    def adjust_stock(name: str, amount: float, operator: str, comment: str) -> bool:
        try:
            payload = {"powder_name": name, "quantity_change": amount, "operator": operator, "comment": comment}
            resp = requests.post(f"{API_URL}/inventory/adjust/", json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            print(e)
            return False

    @staticmethod
    def delete_powder(name: str) -> bool:
        try:
            resp = requests.delete(f"{API_URL}/powders/{name}", timeout=5)
            return resp.status_code == 200
        except:
            return False


class OperationDialog(QDialog):
    def __init__(self, parent, mode: str, powder_name: str = "", current_stock: float = 0.0):
        super().__init__(parent)
        self.mode = mode  # 'restock', 'adjust', 'add'
        self.setWindowTitle(
            f"{'Restock' if mode == 'restock' else 'Adjustment' if mode == 'adjust' else 'Add New Powder'}")
        self.setModal(True)
        self.resize(400, 300)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.op_input = QComboBox() if mode != 'add' else None
        self.amount_input = QLineEdit()
        self.comment_input = QLineEdit()
        self.name_input = QLineEdit()
        self.dens_input = QLineEdit()
        self.factor_input = QLineEdit()
        self.gpm_input = QLineEdit()

        if mode == 'add':
            self.name_input.setText("")
            form.addRow("Название:", self.name_input)
            form.addRow("Плотность:", self.dens_input)
            form.addRow("Коэффициент подачи:", self.factor_input)
            form.addRow("Целевое GPM:", self.gpm_input)
        else:
            lbl_info = QLabel(f"Порошок: {powder_name}\nТекущий ассортимент: {current_stock:.2f} г")
            lbl_info.setStyleSheet("font-weight: bold; color: #2c3e50;")
            form.addRow(lbl_info)

            self.op_input = QComboBox()
            self.op_input.addItems(["Администратор", "Кладовщик", "Технолог"])
            form.addRow("Оператор:", self.op_input)

            self.amount_input.setPlaceholderText("Введите количество (г)")
            form.addRow("Количество (г):", self.amount_input)

            if mode == 'adjust':
                self.comment_input.setPlaceholderText("Причина корректировки")
                form.addRow("Комментарий:", self.comment_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.result_data = {}

    def get_data(self):
        if self.mode == 'add':
            try:
                return {
                    "name": self.name_input.text(),
                    "density": float(self.dens_input.text()),
                    "factor": float(self.factor_input.text()),
                    "gpm": float(self.gpm_input.text())
                }
            except ValueError:
                return None
        else:
            try:
                amt = float(self.amount_input.text())
                if self.mode == 'restock' and amt <= 0:
                    raise ValueError
                return {
                    "operator": self.op_input.currentText(),
                    "amount": amt,
                    "comment": self.comment_input.text()
                }
            except ValueError:
                return None


class AdminPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Управление базой данных и складскими запасами")
        self.setGeometry(150, 150, 1100, 700)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        tabs = QTabWidget()
        tabs.addTab(self.create_inventory_tab(), "Инвентаризация и складские запасы")
        tabs.addTab(self.create_logs_tab(), "История транзакций")
        tabs.addTab(self.create_settings_tab(), "Настройки порошка")

        main_layout.addWidget(tabs)

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(3000)  # Auto-refresh every 3s

        self.refresh_all()

    def create_inventory_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.stock_table = QTableWidget()
        self.stock_table.setColumnCount(4)
        self.stock_table.setHorizontalHeaderLabels(["Порошок", "Количество (г)", "Статус", "Действия"])
        self.stock_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.stock_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.stock_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.stock_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        toolbar = QHBoxLayout()
        btn_refresh = QPushButton("Обновить")
        btn_refresh.clicked.connect(self.refresh_all)
        toolbar.addWidget(btn_refresh)
        toolbar.addStretch()

        layout.addLayout(toolbar)
        layout.addWidget(self.stock_table)
        return widget

    def create_logs_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(5)
        self.log_table.setHorizontalHeaderLabels(["Отметка времени", "Порошок", "Изменение (г)", "Оператор", "Тип"])
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.log_table)
        return widget

    def create_settings_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.settings_table = QTableWidget()
        self.settings_table.setColumnCount(6)
        self.settings_table.setHorizontalHeaderLabels(["ID", "Название", "Плотность", "Коэффициент", "Целевое GPM", "Действие"])
        self.settings_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        toolbar = QHBoxLayout()
        btn_add = QPushButton("Добавить новый материал")
        btn_add.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        btn_add.clicked.connect(self.open_add_dialog)
        toolbar.addWidget(btn_add)
        toolbar.addStretch()

        layout.addLayout(toolbar)
        layout.addWidget(self.settings_table)
        return widget

    def refresh_all(self):
        self.refresh_inventory()
        self.refresh_logs()
        self.refresh_settings()

    def refresh_inventory(self):
        stock = AdminNetwork.get_stock()
        self.stock_table.setRowCount(len(stock))
        for i, s in enumerate(stock):
            self.stock_table.setItem(i, 0, QTableWidgetItem(s.powder_name))
            self.stock_table.setItem(i, 1, QTableWidgetItem(f"{s.quantity_grams:.2f}"))

            status_item = QTableWidgetItem("OK")
            if s.quantity_grams < 500:
                status_item.setText("МАЛО")
                status_item.setForeground(QColor("red"))
                status_item.setBackground(QColor("#ffcccc"))
            elif s.quantity_grams < 1000:
                status_item.setText("Средне")
                status_item.setForeground(QColor("orange"))
            self.stock_table.setItem(i, 2, status_item)

            # Кнопки действий
            actions_widget = QWidget()
            act_layout = QHBoxLayout(actions_widget)
            act_layout.setContentsMargins(0, 0, 0, 0)

            btn_restock = QPushButton("+")
            btn_restock.setToolTip("Пополнение запасов")
            btn_restock.setFixedSize(30, 30)
            btn_restock.clicked.connect(
                lambda checked, name=s.powder_name, qty=s.quantity_grams: self.open_operation_dialog('restock', name,
                                                                                                     qty))

            btn_adjust = QPushButton("±")
            btn_adjust.setToolTip("Регулировать")
            btn_adjust.setFixedSize(30, 30)
            btn_adjust.clicked.connect(
                lambda checked, name=s.powder_name, qty=s.quantity_grams: self.open_operation_dialog('adjust', name,
                                                                                                     qty))

            act_layout.addWidget(btn_restock)
            act_layout.addWidget(btn_adjust)
            self.stock_table.setCellWidget(i, 3, actions_widget)

    def refresh_logs(self):
        logs = AdminNetwork.get_logs()
        self.log_table.setRowCount(len(logs))
        for i, l in enumerate(logs):
            self.log_table.setItem(i, 0, QTableWidgetItem(l.timestamp[:19]))
            self.log_table.setItem(i, 1, QTableWidgetItem(l.powder_name))

            change_item = QTableWidgetItem(f"{l.change:+.2f}")
            if l.change > 0:
                change_item.setForeground(QColor("green"))
                change_item.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            else:
                change_item.setForeground(QColor("red"))
            self.log_table.setItem(i, 2, change_item)

            self.log_table.setItem(i, 3, QTableWidgetItem(l.operator))

            type_text = "Пополнение запасов" if l.change > 0 else "Потребление" if l.change < 0 else "Корректировка"
            self.log_table.setItem(i, 4, QTableWidgetItem(type_text))

    def refresh_settings(self):
        powders = AdminNetwork.get_powders()
        self.settings_table.setRowCount(len(powders))
        for i, p in enumerate(powders):
            self.settings_table.setItem(i, 0, QTableWidgetItem(str(p.id)))
            self.settings_table.setItem(i, 1, QTableWidgetItem(p.name))
            self.settings_table.setItem(i, 2, QTableWidgetItem(f"{p.density:.2f}"))
            self.settings_table.setItem(i, 3, QTableWidgetItem(f"{p.flow_factor:.2f}"))
            self.settings_table.setItem(i, 4, QTableWidgetItem(f"{p.target_gpm:.1f}"))

            btn_del = QPushButton("Удалить")
            btn_del.setStyleSheet("background-color: #c0392b; color: white;")
            btn_del.setFixedSize(60, 25)
            btn_del.clicked.connect(lambda checked, name=p.name: self.delete_powder(name))
            self.settings_table.setCellWidget(i, 5, btn_del)

    def open_operation_dialog(self, mode: str, name: str, current: float):
        dlg = OperationDialog(self, mode, name, current)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data:
                QMessageBox.critical(self, "Ошибка", "Неверные входные данные")
                return

            if mode == 'restock':
                ok = AdminNetwork.adjust_stock(name, data['amount'], data['operator'], "Пополнение запасов")
                msg = f"Добавлено {data['amount']}г в {name}"
            elif mode == 'adjust':
                ok = AdminNetwork.adjust_stock(name, data['amount'], data['operator'], data['comment'])
                msg = f"Скорректировано {name} до {data['amount']}г"

            if ok:
                QMessageBox.information(self, "Успешно", msg)
                self.refresh_all()
            else:
                QMessageBox.critical(self, "Ошибка", "Операция не удалась. Проверьте журналы сервера.")

    def open_add_dialog(self):
        dlg = OperationDialog(self, 'add')
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data:
                QMessageBox.critical(self, "Ошибка", "Недопустимые числовые значения")
                return
            if AdminNetwork.add_powder(data['name'], data['density'], data['factor'], data['gpm']):
                QMessageBox.information(self, "Успешно", f"Порошок {data['name']} добавлен с учетом стандартного запаса в 5 кг.")
                self.refresh_all()
            else:
                QMessageBox.critical(self, "Ошибка", "Не удалось добавить (возможно, имя уже существует)")

    def delete_powder(self, name: str):
        reply = QMessageBox.question(self, "Подтвердить удаление",
                                     f"Вы уверены, что хотите удалить? '{name}'?\nКоличество должно быть равно 0.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if AdminNetwork.delete_powder(name):
                QMessageBox.information(self, "Успешно", "Порошок удален")
                self.refresh_all()
            else:
                QMessageBox.critical(self, "Ошибка", "Удалить невозможно. Количество на складе не равно нулю или произошла ошибка сервера.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = AdminPanel()
    win.show()
    sys.exit(app.exec())