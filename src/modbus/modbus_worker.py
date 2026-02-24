# modbus_worker.py
from PyQt5.QtCore import QThread, pyqtSignal
from typing import Optional
from src.modbus.modbus_feeder import ModbusFeederClient, FeederStatus
import logging

logger = logging.getLogger("ModbusWorker")


class ModbusWorker(QThread):
    status_signal = pyqtSignal(object)
    connected_signal = pyqtSignal(bool)
    error_signal = pyqtSignal(str)

    def __init__(self, port: str, baudrate: int = 115200, slave_id: int = 1):
        super().__init__()
        self.client = ModbusFeederClient(port, baudrate, slave_id)
        self.running = False
        self.poll_interval = 500

    def run(self):
        if not self.client.connect():
            self.connected_signal.emit(False)
            self.error_signal.emit("Не удалось подключиться к дозатору")
            return

        self.connected_signal.emit(True)
        self.running = True

        while self.running:
            try:
                status = self.client.get_status()
                if status:
                    self.status_signal.emit(status)
                else:
                    logger.warning("Modbus: Не удалось получить статус")
            except Exception as e:
                logger.error(f"Modbus опрос ошибка: {e}")
                self.error_signal.emit(str(e))

            self.msleep(self.poll_interval)

        self.client.disconnect()

    def stop(self):
        self.running = False
        self.wait(1000)
        if self.isRunning():
            self.terminate()

    def start_feeder(self) -> bool:
        return self.client.start()

    def stop_feeder(self) -> bool:
        return self.client.stop()

    def reset_feeder(self) -> bool:
        return self.client.reset()

    def set_speed(self, rpm: float) -> bool:
        return self.client.set_speed(rpm)

    def is_connected(self) -> bool:
        return self.client.connected