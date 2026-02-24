# modbus_feeder.py
import logging
from dataclasses import dataclass
from typing import Optional
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

logger = logging.getLogger("ModbusFeeder")


@dataclass
class FeederStatus:
    ready: bool = False
    running: bool = False
    alarm: bool = False
    rpm: float = 0.0
    model: int = 0
    firmware_version: int = 0
    serial_number: int = 0


class ModbusFeederClient:
    COIL_READY = 0
    COIL_RUNNING = 1
    COIL_ALARM = 2
    COIL_START_STOP = 917
    COIL_RESET = 971

    IR_SPEED_MONITOR = 288
    IR_MODEL = 910
    IR_SERIAL = 911
    IR_FIRMWARE_VER = 913

    HR_SPEED_SETPOINT = 266
    HR_MAX_SPEED = 256
    HR_MODBUS_ADDR = 950
    HR_BAUDRATE = 951

    def __init__(self, port: str = "COM3", baudrate: int = 115200,
                 slave_id: int = 1, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.slave_id = slave_id
        self.timeout = timeout
        self.client: Optional[ModbusSerialClient] = None
        self.connected = False

    def connect(self) -> bool:
        try:
            self.client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self.timeout
            )
            if self.client.connect():
                self.connected = True
                logger.info(f"Modbus: Подключено к {self.port} @ {self.baudrate}")
                return True
        except Exception as e:
            logger.error(f"Modbus ошибка подключения: {e}")
        self.connected = False
        return False

    def disconnect(self):
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("Modbus: Отключено")

    def _check_connection(self) -> bool:
        if not self.connected or not self.client:
            logger.warning("Modbus: Нет подключения")
            return False
        return True

    def read_coils(self, address: int, count: int = 1) -> Optional[list]:
        if not self._check_connection():
            return None
        try:
            result = self.client.read_coils(address, count, slave=self.slave_id)
            if result.isError():
                return None
            return result.bits[:count]
        except ModbusException as e:
            logger.error(f"Modbus read_coils error: {e}")
            return None

    def write_coil(self, address: int, value: bool) -> bool:
        if not self._check_connection():
            return False
        try:
            result = self.client.write_coil(address, value, slave=self.slave_id)
            return not result.isError()
        except ModbusException as e:
            logger.error(f"Modbus write_coil error: {e}")
            return False

    def read_input_registers(self, address: int, count: int = 1) -> Optional[list]:
        if not self._check_connection():
            return None
        try:
            result = self.client.read_input_registers(address, count, slave=self.slave_id)
            if result.isError():
                return None
            return result.registers[:count]
        except ModbusException as e:
            logger.error(f"Modbus read_input_registers error: {e}")
            return None

    def write_holding_register(self, address: int, value: int) -> bool:
        if not self._check_connection():
            return False
        try:
            result = self.client.write_register(address, value, slave=self.slave_id)
            return not result.isError()
        except ModbusException as e:
            logger.error(f"Modbus write_register error: {e}")
            return False

    def start(self) -> bool:
        return self.write_coil(self.COIL_START_STOP, True)

    def stop(self) -> bool:
        return self.write_coil(self.COIL_START_STOP, False)

    def reset(self) -> bool:
        return self.write_coil(self.COIL_RESET, True)

    def get_status(self) -> Optional[FeederStatus]:
        if not self._check_connection():
            return None

        coils = self.read_coils(self.COIL_READY, 3)
        if not coils:
            return None

        speed_regs = self.read_input_registers(self.IR_SPEED_MONITOR, 1)
        model_regs = self.read_input_registers(self.IR_MODEL, 1)
        serial_regs = self.read_input_registers(self.IR_SERIAL, 1)
        fw_regs = self.read_input_registers(self.IR_FIRMWARE_VER, 1)

        return FeederStatus(
            ready=bool(coils[0]),
            running=bool(coils[1]),
            alarm=bool(coils[2]),
            rpm=(speed_regs[0] * 0.1) if speed_regs else 0.0,
            model=model_regs[0] if model_regs else 0,
            serial_number=serial_regs[0] if serial_regs else 0,
            firmware_version=fw_regs[0] if fw_regs else 0
        )

    def set_speed(self, rpm: float) -> bool:
        value = int(rpm * 10)
        return self.write_holding_register(self.HR_SPEED_SETPOINT, value)

    def get_speed(self) -> Optional[float]:
        regs = self.read_input_registers(self.IR_SPEED_MONITOR, 1)
        return (regs[0] * 0.1) if regs else None

    def is_ready(self) -> Optional[bool]:
        coils = self.read_coils(self.COIL_READY, 1)
        return coils[0] if coils else None