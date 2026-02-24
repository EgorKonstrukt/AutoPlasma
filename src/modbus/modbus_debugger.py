import sys
import logging
import platform
from datetime import datetime
from typing import Optional, List

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QTextEdit, QComboBox, QSpinBox,
                             QGroupBox, QFormLayout, QFrame, QMessageBox, QCheckBox,
                             QGridLayout, QSizePolicy)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCursor

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ModbusDebugger")


class Regs:
    # Coils (Write/Read)
    COIL_READY = 0
    COIL_RUNNING = 1
    COIL_ALARM = 2
    COIL_START_STOP = 917
    COIL_RESET = 971

    # Input Registers (Read Only)
    IR_SPEED_MONITOR = 288  # 0.1 об/мин
    IR_MODEL = 910  # HEX модель
    IR_SERIAL = 911  # HEX серийный номер
    IR_FW_ID = 912  # ID прошивки
    IR_FW_VER = 913  # Версия прошивки (битовая маска)
    IR_MIN_SPEED = 256  # Мин скорость
    IR_MAX_SPEED_USER = 256  # Макс скорость (HR тоже есть, но тут мониторим)

    # Holding Registers (Read/Write)
    HR_SPEED_SETPOINT = 266  # Задание скорости 0.1 об/мин
    HR_MAX_SPEED = 256  # Макс скорость пользователя
    HR_WATCHDOG = 914  # Таймаут watchdog


class ModbusWorker(QThread):
    signal_log = pyqtSignal(str, str)
    signal_status = pyqtSignal(dict)
    signal_connected = pyqtSignal(bool)

    def __init__(self, port: str, baudrate: int, slave_id: int):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.client: Optional[ModbusSerialClient] = None
        self.running = False
        self.poll_interval = 500  # мс
        self._stop_requested = False

    def run(self):
        self._connect()
        while not self._stop_requested:
            if self.client and self.client.connected:
                self._poll_device()
            self.msleep(self.poll_interval)

        self._disconnect()

    def _connect(self):
        try:
            self.client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1.0
            )
            if self.client.connect():
                self.signal_log.emit(f"Подключено к {self.port} @ {self.baudrate}", "success")
                self.signal_connected.emit(True)
            else:
                raise ConnectionException("Не удалось установить соединение")
        except Exception as e:
            self.signal_log.emit(f"Ошибка подключения: {str(e)}", "error")
            self.signal_connected.emit(False)

    def _disconnect(self):
        if self.client:
            self.client.close()
            self.signal_log.emit("Соединение разорвано", "info")
            self.signal_connected.emit(False)

    def stop_thread(self):
        self._stop_requested = True
        self.wait(1000)

    def _poll_device(self):
        try:
            # Чтение статусов (Coils 0, 1, 2)
            coils = self.client.read_coils(Regs.COIL_READY, 3, slave=self.slave_id)
            if coils.isError(): raise ModbusException("Error reading coils")

            # Читаем блок: 288 (скорость), 910 (модель), 911 (серийник), 912 (fw id), 913 (fw ver)
            speed_reg = self.client.read_input_registers(Regs.IR_SPEED_MONITOR, 1, slave=self.slave_id)
            model_reg = self.client.read_input_registers(Regs.IR_MODEL, 1, slave=self.slave_id)
            fw_ver_reg = self.client.read_input_registers(Regs.IR_FW_VER, 1, slave=self.slave_id)

            if speed_reg.isError() or model_reg.isError():
                raise ModbusException("Error reading registers")

            speed_val = speed_reg.registers[0] * 0.1
            model_val = hex(model_reg.registers[0])

            # Декодирование версии прошивки (см. спецификацию табл. 2)
            fw_raw = fw_ver_reg.registers[0]
            major = (fw_raw >> 11) & 0xF
            minor = (fw_raw >> 4) & 0x7F
            micro = fw_raw & 0xF
            fw_str = f"{major}.{minor}.{micro}"

            status_data = {
                "ready": bool(coils.bits[0]),
                "running": bool(coils.bits[1]),
                "alarm": bool(coils.bits[2]),
                "speed": speed_val,
                "model": model_val,
                "firmware": fw_str
            }
            self.signal_status.emit(status_data)

        except Exception as e:
            pass

    def cmd_start(self):
        if not self.client or not self.client.connected: return False
        try:
            res = self.client.write_coil(Regs.COIL_START_STOP, True, slave=self.slave_id)
            if not res.isError():
                self.signal_log.emit("Команда: СТАРТ отправлена", "success")
                return True
            else:
                self.signal_log.emit("Ошибка отправки команды СТАРТ", "error")
        except Exception as e:
            self.signal_log.emit(f"Ошибка СТАРТ: {e}", "error")
        return False

    def cmd_stop(self):
        if not self.client or not self.client.connected: return False
        try:
            res = self.client.write_coil(Regs.COIL_START_STOP, False, slave=self.slave_id)
            if not res.isError():
                self.signal_log.emit("Команда: СТОП отправлена", "success")
                return True
            else:
                self.signal_log.emit("Ошибка отправки команды СТОП", "error")
        except Exception as e:
            self.signal_log.emit(f"Ошибка СТОП: {e}", "error")
        return False

    def cmd_reset(self):
        if not self.client or not self.client.connected: return False
        try:
            res = self.client.write_coil(Regs.COIL_RESET, True, slave=self.slave_id)
            if not res.isError():
                self.signal_log.emit("Команда: СБРОС отправлена", "success")
                return True
            else:
                self.signal_log.emit("Ошибка отправки команды СБРОС", "error")
        except Exception as e:
            self.signal_log.emit(f"Ошибка СБРОС: {e}", "error")
        return False

    def cmd_set_speed(self, rpm: float):
        if not self.client or not self.client.connected: return False
        try:
            val = int(rpm * 10)
            res = self.client.write_register(Regs.HR_SPEED_SETPOINT, val, slave=self.slave_id)
            if not res.isError():
                self.signal_log.emit(f"Установка скорости: {rpm} об/мин ({val})", "success")
                return True
            else:
                self.signal_log.emit("Ошибка установки скорости", "error")
        except Exception as e:
            self.signal_log.emit(f"Ошибка скорости: {e}", "error")
        return False


class DebuggerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Отладчик Modbus USB/485 - Дозатор 7103")
        self.setGeometry(100, 100, 900, 700)

        self.worker: Optional[ModbusWorker] = None
        self.is_connected = False

        self.init_ui()
        self.scan_ports()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        conn_group = QGroupBox("Параметры подключения")
        conn_layout = QFormLayout()

        self.combo_port = QComboBox()
        self.combo_port.setEditable(True)
        conn_layout.addRow("COM Порт:", self.combo_port)

        self.spin_baud = QSpinBox()
        self.spin_baud.setRange(1200, 230400)
        self.spin_baud.setValue(115200)
        self.spin_baud.setSingleStep(1200)
        conn_layout.addRow("Битрейт:", self.spin_baud)

        self.spin_slave = QSpinBox()
        self.spin_slave.setRange(1, 247)
        self.spin_slave.setValue(1)
        conn_layout.addRow("Slave ID:", self.spin_slave)

        self.btn_connect = QPushButton("Подключиться")
        self.btn_connect.setCheckable(True)
        self.btn_connect.clicked.connect(self.toggle_connection)
        conn_layout.addRow("", self.btn_connect)

        conn_group.setLayout(conn_layout)
        main_layout.addWidget(conn_group)

        mid_layout = QHBoxLayout()

        status_group = QGroupBox("Текущее состояние устройства")
        status_layout = QGridLayout()

        self.lbl_ready = QLabel("ГОТОВ: --")
        self.lbl_running = QLabel("РАБОТА: --")
        self.lbl_alarm = QLabel("АВАРИЯ: --")
        self.lbl_speed = QLabel("Скорость: -- об/мин")
        self.lbl_model = QLabel("Модель: --")
        self.lbl_fw = QLabel("Прошивка: --")

        font_status = QFont("Consolas", 12, QFont.Bold)
        for lbl in [self.lbl_ready, self.lbl_running, self.lbl_alarm, self.lbl_speed, self.lbl_model, self.lbl_fw]:
            lbl.setFont(font_status)
            lbl.setStyleSheet("padding: 5px; background: #2c3e50; color: white; border-radius: 4px;")

        status_layout.addWidget(self.lbl_ready, 0, 0)
        status_layout.addWidget(self.lbl_running, 0, 1)
        status_layout.addWidget(self.lbl_alarm, 0, 2)
        status_layout.addWidget(self.lbl_speed, 1, 0, 1, 3)
        status_layout.addWidget(self.lbl_model, 2, 0, 1, 2)
        status_layout.addWidget(self.lbl_fw, 2, 2, 1, 1)

        status_group.setLayout(status_layout)
        mid_layout.addWidget(status_group, 1)

        ctrl_group = QGroupBox("Управление")
        ctrl_layout = QVBoxLayout()

        self.spin_speed_set = QSpinBox()
        self.spin_speed_set.setRange(0, 5000)
        self.spin_speed_set.setValue(0)
        self.spin_speed_set.setSuffix(" об/мин")
        ctrl_layout.addWidget(QLabel("Задание скорости:"))
        ctrl_layout.addWidget(self.spin_speed_set)

        btn_layout = QGridLayout()
        self.btn_start = QPushButton("СТАРТ")
        self.btn_start.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; padding: 10px;")
        self.btn_start.clicked.connect(self.do_start)

        self.btn_stop = QPushButton("СТОП")
        self.btn_stop.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 10px;")
        self.btn_stop.clicked.connect(self.do_stop)

        self.btn_reset = QPushButton("СБРОС")
        self.btn_reset.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 10px;")
        self.btn_reset.clicked.connect(self.do_reset)

        self.btn_read_once = QPushButton("Опросить сейчас")
        self.btn_read_once.clicked.connect(self.force_poll)

        btn_layout.addWidget(self.btn_start, 0, 0)
        btn_layout.addWidget(self.btn_stop, 0, 1)
        btn_layout.addWidget(self.btn_reset, 1, 0, 1, 2)
        btn_layout.addWidget(self.btn_read_once, 2, 0, 1, 2)

        ctrl_layout.addLayout(btn_layout)
        ctrl_layout.addStretch()
        ctrl_group.setLayout(ctrl_layout)
        mid_layout.addWidget(ctrl_group, 1)

        main_layout.addLayout(mid_layout)

        log_group = QGroupBox("Журнал событий")
        log_layout = QVBoxLayout()
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setFont(QFont("Consolas", 9))
        self.text_log.setStyleSheet("background-color: #1a1a1a; color: #00ff00;")
        log_layout.addWidget(self.text_log)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group, 1)

    def scan_ports(self):
        self.combo_port.clear()
        try:
            import serial.tools.list_ports
            ports = serial.tools.list_ports.comports()
            for p in ports:
                self.combo_port.addItem(f"{p.device} - {p.description}")
            if ports:
                self.log("Доступные порты найдены.", "info")
            else:
                self.log("Нет доступных COM портов.", "error")
                if platform.system() == "Windows":
                    self.combo_port.addItem("COM1")
                    self.combo_port.addItem("COM2")
                    self.combo_port.addItem("COM3")
        except ImportError:
            self.log("Модуль pyserial не найден. Введите порт вручную.", "error")
            if platform.system() == "Windows":
                for i in range(1, 10):
                    self.combo_port.addItem(f"COM{i}")
            else:
                self.combo_port.addItem("/dev/ttyUSB0")
                self.combo_port.addItem("/dev/ttyS0")

    def toggle_connection(self):
        if self.btn_connect.isChecked():
            port = self.combo_port.currentText().split(" - ")[0].strip()
            baud = self.spin_baud.value()
            slave = self.spin_slave.value()

            self.log(f"Попытка подключения к {port} @ {baud}...", "info")
            self.worker = ModbusWorker(port, baud, slave)
            self.worker.signal_log.connect(self.log)
            self.worker.signal_status.connect(self.update_status_ui)
            self.worker.signal_connected.connect(self.on_connection_state)
            self.worker.start()

            self.btn_connect.setText("Отключиться")
            self.btn_connect.setStyleSheet("background-color: #e74c3c; color: white;")
            self.enable_controls(True)
        else:
            if self.worker:
                self.worker.stop_thread()
                self.worker = None
            self.btn_connect.setText("Подключиться")
            self.btn_connect.setStyleSheet("")
            self.enable_controls(False)
            self.reset_status_ui()

    def on_connection_state(self, connected: bool):
        self.is_connected = connected
        if not connected:
            self.btn_connect.setChecked(False)
            self.btn_connect.setText("Подключиться")
            self.btn_connect.setStyleSheet("")
            self.enable_controls(False)

    def enable_controls(self, enabled: bool):
        self.btn_start.setEnabled(enabled)
        self.btn_stop.setEnabled(enabled)
        self.btn_reset.setEnabled(enabled)
        self.btn_read_once.setEnabled(enabled)
        self.spin_speed_set.setEnabled(enabled)
        self.combo_port.setEnabled(not enabled)
        self.spin_baud.setEnabled(not enabled)
        self.spin_slave.setEnabled(not enabled)

    def do_start(self):
        if self.worker: self.worker.cmd_start()

    def do_stop(self):
        if self.worker: self.worker.cmd_stop()

    def do_reset(self):
        if self.worker: self.worker.cmd_reset()

    def set_speed(self):
        if self.worker:
            rpm = self.spin_speed_set.value()
            self.worker.cmd_set_speed(float(rpm))

    def force_poll(self):
        if self.worker and self.worker.client and self.worker.client.connected:
            self.log("Принудительный опрос...", "info")
            pass

    def update_status_ui(self, data: dict):
        # Ready
        if data['ready']:
            self.lbl_ready.setText("ГОТОВ: ДА")
            self.lbl_ready.setStyleSheet(
                "background: #2ecc71; color: white; font-weight: bold; padding: 5px; border-radius: 4px;")
        else:
            self.lbl_ready.setText("ГОТОВ: НЕТ")
            self.lbl_ready.setStyleSheet("background: #7f8c8d; color: white; padding: 5px; border-radius: 4px;")

        if data['running']:
            self.lbl_running.setText("РАБОТА: ДА")
            self.lbl_running.setStyleSheet(
                "background: #3498db; color: white; font-weight: bold; padding: 5px; border-radius: 4px;")
        else:
            self.lbl_running.setText("РАБОТА: НЕТ")
            self.lbl_running.setStyleSheet("background: #7f8c8d; color: white; padding: 5px; border-radius: 4px;")

        if data['alarm']:
            self.lbl_alarm.setText("АВАРИЯ: ДА")
            self.lbl_alarm.setStyleSheet(
                "background: #e74c3c; color: white; font-weight: bold; padding: 5px; border-radius: 4px;")
        else:
            self.lbl_alarm.setText("АВАРИЯ: НЕТ")
            self.lbl_alarm.setStyleSheet(
                "background: #2c3e50; color: #2ecc71; font-weight: bold; padding: 5px; border-radius: 4px;")

        self.lbl_speed.setText(f"Скорость: {data['speed']:.1f} об/мин")
        self.lbl_model.setText(f"Модель: {data['model']}")
        self.lbl_fw.setText(f"Прошивка: v{data['firmware']}")

        # if not self.spin_speed_set.hasFocus():
        #     self.spin_speed_set.setValue(int(data['speed']))

    def reset_status_ui(self):
        self.lbl_ready.setText("ГОТОВ: --")
        self.lbl_running.setText("РАБОТА: --")
        self.lbl_alarm.setText("АВАРИЯ: --")
        self.lbl_speed.setText("Скорость: -- об/мин")
        self.lbl_model.setText("Модель: --")
        self.lbl_fw.setText("Прошивка: --")
        for lbl in [self.lbl_ready, self.lbl_running, self.lbl_alarm]:
            lbl.setStyleSheet("background: #7f8c8d; color: white; padding: 5px; border-radius: 4px;")

    def log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "#00ff00" if level == "success" else ("#ff5555" if level == "error" else "#aaaaaa")
        html = f'<span style="color:{color}">[{timestamp}] {message}</span><br>'
        self.text_log.append(html)
        self.text_log.moveCursor(QTextCursor.End)

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop_thread()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = DebuggerWindow()
    window.show()
    sys.exit(app.exec())