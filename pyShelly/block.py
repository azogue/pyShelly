"""Block is a physical device"""
# pylint: disable=broad-except, bare-except

from datetime import datetime
from .utils import shelly_http_get
from .switch import Switch
from .relay import Relay
from .powermeter import PowerMeter
from .sensor import (Sensor, BinarySensor, Flood, DoorWindow, ExtTemp,
                     ExtHumidity, Gas)
from .light import RGBW2W, RGBW2C, RGBWW, Bulb, Duo, Vintage
from .dimmer import Dimmer
from .roller import Roller
from .utils import exception_log
from .base import Base

from .const import (
    LOGGER,
    SENSOR_UNAVAILABLE_SEC,
    INFO_VALUE_DEVICE_TEMP,
    INFO_VALUE_CLOUD_STATUS,
    INFO_VALUE_CLOUD_ENABLED,
    INFO_VALUE_CLOUD_CONNECTED,
    INFO_VALUE_HAS_FIRMWARE_UPDATE,
    INFO_VALUE_FW_VERSION,
    INFO_VALUE_LATEST_FIRMWARE_VERSION,
    INFO_VALUE_LATEST_BETA_FW_VERSION,
    INFO_VALUE_PAYLOAD,
    INFO_VALUE_BATTERY,
    INFO_VALUE_ILLUMINANCE,
    INFO_VALUE_TILT,
    INFO_VALUE_VIBRATION,
    INFO_VALUE_TEMP,
    INFO_VALUE_PPM,
    INFO_VALUE_SENSOR,
    INFO_VALUE_TOTAL_WORK_TIME,
    ATTR_PATH,
    ATTR_FMT,
    ATTR_POS,
    ATTR_AUTO_SET,
    BLOCK_INFO_VALUES,
    SHELLY_TYPES
)

class Block(Base):
    def __init__(self, parent, block_id, block_type, ip_addr, discovery_src):
        super(Block, self).__init__()
        self.id = block_id
        self.unit_id = block_id
        self.type = block_type
        self.parent = parent
        self.ip_addr = ip_addr
        self.devices = []
        self.discovery_src = discovery_src
        self.protocols = []
        self.cb_updated = []
        self.unavailable_after_sec = None
        #self.info_values = {}
        #self.info_values_updated = {}
        self.last_update_status_info = None
        self.reload = False
        self.last_updated = None #datetime.now()
        self.error = None
        self.discover_by_mdns = False
        self.discover_by_coap = False
        self.sleep_device = False
        self.payload = None
        self.settings = None
        self.exclude_info_values = []
        #self._info_value_cfg = None
        self._setup()
        self._available = None

    def update_coap(self, payload, ip_addr):
        self.ip_addr = ip_addr  # If changed ip
        self.last_updated = datetime.now()
        self._update_info_values_coap(payload)
        for dev in self.devices:
            dev.ip_addr = ip_addr
            dev._update_info_values_coap(payload)
            if hasattr(dev, 'update_coap'):
                dev.update_coap(payload)
        if self.reload:
            self._reload_devices()
            for dev in self.devices:
                dev._update_info_values_coap(payload)
                if hasattr(dev, 'update_coap'):
                    dev.update_coap(payload)
                self.parent.add_device(dev, self.discovery_src)
            self.reload = False

    def raise_updated(self):
        for callback in self.cb_updated:
            callback(self)

    def loop(self):
        if self._info_value_cfg:
            for iv, cfg in self._info_value_cfg.items():
                if ATTR_AUTO_SET in cfg:
                    value = self.info_values.get(iv)
                    param = cfg.get(ATTR_AUTO_SET)
                    new_val = param[0]
                    if value != new_val:
                        time = self.info_values_updated.get(iv)
                        delay = param[1]
                        if time:
                            diff = datetime.now() - time
                            if diff.total_seconds() > delay:
                                self.info_values[iv] = new_val
                                self.raise_updated()

    def check_available(self):
        if self.available() != self._available:
            self._available = self.available()
            self.raise_updated()
            for dev in self.devices:
                dev.raise_updated()

    def update_status_information(self):
        """Update the status information."""
        self.last_update_status_info = datetime.now()

        LOGGER.debug("Get status from %s %s", self.id, self.friendly_name())
        success, status = self.http_get('/status', False)

        if not success or status == {}:
            return

        if 'poll' not in self.protocols:
            self.protocols.append("poll")

        self.last_updated = datetime.now()

        #Put status in info_values
        for name, cfg in BLOCK_INFO_VALUES.items():
            if name in self.exclude_info_values:
                continue
            self._update_info_value(name, status, cfg)

        if self._info_value_cfg:
            for name, cfg in self._info_value_cfg.items():
                self._update_info_value(name, status, cfg)

        if self.payload:
            self.info_values[INFO_VALUE_PAYLOAD] = self.payload

        if self.info_values.get(INFO_VALUE_CLOUD_ENABLED):
            if self.info_values.get(INFO_VALUE_CLOUD_CONNECTED):
                self.info_values[INFO_VALUE_CLOUD_STATUS] = 'connected'
            else:
                self.info_values[INFO_VALUE_CLOUD_STATUS] = 'disconnected'
        else:
            self.info_values[INFO_VALUE_CLOUD_STATUS] = 'disabled'

        #self.info_values[INFO_VALUE_HAS_FIRMWARE_UPDATE] = self.has_fw_update()
        self.info_values[INFO_VALUE_LATEST_BETA_FW_VERSION] = \
            self.latest_fw_version(True)

        self.raise_updated()

        for dev in self.devices:
            try:
                if dev._info_value_cfg:
                    for name, cfg in dev._info_value_cfg.items():
                        dev._update_info_value(name, status, cfg)
                dev.update_status_information(status)
                dev.raise_updated()
            except Exception as ex:
                exception_log(ex, "Error update device status: {} {}", \
                    dev.id, dev.type)

    def http_get(self, url, log_error=True):
        """Send HTTP GET request"""
        success, res = shelly_http_get(self.ip_addr, url, \
                              self.parent.username, self.parent.password, \
                              log_error)
        return success, res

    def update_firmware(self, beta = False):
        """Start firmware update"""
        url = None
        if beta:
            url = self.parent._firmware_mgr.url(self.type, False)
        if url:
            self.http_get("/ota?url=" + url)
        else:
            self.http_get("/ota?update=1")

    def poll_settings(self):
        if self.type in SHELLY_TYPES and \
            SHELLY_TYPES[self.type].get('battery'):
                return
        success, settings = self.http_get("/settings")
        if success:
            self.settings = settings

    def _setup(self):
        #Get settings
        self.poll_settings()
        #Shelly BULB
        if self.type == 'SHBLB-1' or self.type == 'SHCL-255':
            self._add_device(Bulb(self))
        #Shelly 1
        elif self.type == 'SHSW-1' or self.type == 'SHSK-1':
            self._add_device(Relay(self, 0, [112,1101], None, [118, 2101]))
            self._add_device(Switch(self, 0, [118, 2101], 2102, 2103))
            self._add_device(ExtTemp(self, 0), True)
            self._add_device(ExtTemp(self, 1), True)
            self._add_device(ExtTemp(self, 2), True)
            self._add_device(ExtHumidity(self, 0), True)
        #Shelly 1 PM
        elif self.type == 'SHSW-PM':
            self._add_device(Relay(self, 0, [112,1101], [111, 4101], [118, 2101]))
            self._add_device(PowerMeter(self, 0, [111, 4101]))
            self._add_device(Switch(self, 0, [118, 2101], 2102, 2103))
            self._add_device(ExtTemp(self, 0), True)
            self._add_device(ExtTemp(self, 1), True)
            self._add_device(ExtTemp(self, 2), True)
            self._add_device(ExtHumidity(self, 0), True)
        #Shelly 2
        elif self.type == 'SHSW-21':
            if self.settings:
                if self.settings.get('mode') == 'roller':
                    self._add_device(Roller(self))
                else:
                    self._add_device(Relay(self, 1, [112,1101], [111, 4101], [118, 2101]))
                    self._add_device(Relay(self, 2, [122,1201], [111, 4101], [128, 2201]))
                self._add_device(Switch(self, 1, [118, 2101], 2102, 2103))
                self._add_device(Switch(self, 2, [118, 2201], 2202, 2203))
                self._add_device(PowerMeter(self, 0, [111, 4101]))
        #Shelly 2.5
        elif self.type == 'SHSW-25':
            if self.settings:
                if self.settings.get('mode') == 'roller':
                    self._add_device(Roller(self))
                    self._add_device(PowerMeter(self, 1, [111, 121, 4101, 4102], [0, 1]))
                else:
                    self._add_device(Relay(self, 1, [112,1101], [111, 4101], [118, 2101]))
                    self._add_device(Relay(self, 2, [122,1201], [121, 4201], [128, 2201]))
                    self._add_device(PowerMeter(self, 1, [111, 4101]))
                    self._add_device(PowerMeter(self, 2, [121, 4201]))
                    self._add_device(Switch(self, 1, [118, 2101], 2102, 2103))
                    self._add_device(Switch(self, 2, [128, 2201], 2202, 2203))
                #self._add_device(InfoSensor(self, 'temperature'))
            #todo delayed reload
        #Shelly PLUG'S
        elif self.type == 'SHPLG-1' or self.type == 'SHPLG2-1' or \
              self.type == 'SHPLG-S':
            self._add_device(Relay(self, 0, [112,1101], [111, 4101]))
            self._add_device(PowerMeter(self, 0, [111, 4101]))
        #Shelly 3EM
        elif self.type == 'SHEM-3':
            self._add_device(Relay(self, 0, [112,1101]))
            self._add_device(PowerMeter(self, 1, [111, 4105], None, [116, 4108], [114, 4110], [115, 4109], 4106))
            self._add_device(PowerMeter(self, 2, [121, 4205], None, [126, 4208], [124, 4210], [125, 4209], 4206))
            self._add_device(PowerMeter(self, 3, [131, 4305], None, [136, 4308], [134, 4310], [135, 4309], 4306))
        elif self.type == 'SH2LED-1':
            self._add_device(RGBW2W(self, 0))
            self._add_device(RGBW2W(self, 1))
        elif self.type == 'SHEM':
            self._add_device(Relay(self, 0, 112))
            self._add_device(PowerMeter(self, 1, [111], voltage_to_block=True))
            self._add_device(PowerMeter(self, 2, [121], voltage_to_block=True))
        #Shelly 4 Pro
        elif self.type == 'SHSW-44':
            for channel in range(4):
                c10 = channel * 10
                c100 = channel * 100
                relay_pos = [112 + c10, 1101 + c100]
                power_pos = [111 + c10, 4101 + c100]
                switch_pos = [118 + c10, 2101 + c100]
                self._add_device(
                    Relay(self, channel + 1, relay_pos, power_pos, switch_pos))
                self._add_device(PowerMeter(self, channel + 1, power_pos))
                self._add_device(Switch(self, channel + 1, switch_pos))
        elif self.type == 'SHRGBWW-01':
            self._add_device(RGBWW(self))
        #Shelly Dimmer
        elif self.type in ('SHDM-1', 'SHDM-2'):
            self._info_value_cfg = {INFO_VALUE_DEVICE_TEMP : {ATTR_POS : 311}}
            self._add_device(Dimmer(self, 121, 111))
            self._add_device(Switch(self, 1, 131))
            self._add_device(Switch(self, 2, 141))
            self._add_device(PowerMeter(self, 0, None), True)
        elif self.type == 'SHHT-1':
            self.sleep_device = True
            self.unavailable_after_sec = SENSOR_UNAVAILABLE_SEC
            self._add_device(Sensor(self, 33, 'temperature', 'tmp/tC'))
            self._add_device(Sensor(self, 44, 'humidity', 'hum/value'))
        #Shellyy RGBW2
        elif self.type == 'SHRGBW2':
            if self.settings:
                if self.settings.get('mode', 'color') == 'color':
                    self._add_device(RGBW2C(self))
                    self._add_device(PowerMeter(self, 0, [211]))
                else:
                    for channel in range(4):
                        self._add_device(RGBW2W(self, channel + 1))
                        self._add_device(PowerMeter(self, channel+1, \
                                                            [211+channel*10]))
            self._add_device(Switch(self, 0, 118))
            #todo else delayed reload
        #Shelly Flood
        elif self.type == 'SHWT-1':
            self.sleep_device = True
            self.unavailable_after_sec = SENSOR_UNAVAILABLE_SEC
            self._add_device(Flood(self))
            self._add_device(Sensor(self, 33, 'temperature', 'tmp/tC'))
        elif self.type == 'SHDW-1':
            self.sleep_device = True
            self.unavailable_after_sec = SENSOR_UNAVAILABLE_SEC
            self._info_value_cfg = {INFO_VALUE_BATTERY : {ATTR_POS : 77},
                                    INFO_VALUE_TILT : {ATTR_POS : 88},
                                    INFO_VALUE_VIBRATION : {ATTR_POS : 99,
                                                            ATTR_AUTO_SET: [0, 60]},
                                    INFO_VALUE_ILLUMINANCE: {ATTR_POS : 66}
            }
            self._add_device(DoorWindow(self, 55))
        elif self.type == 'SHDW-2':
            self.sleep_device = True
            self.exclude_info_values.append(INFO_VALUE_DEVICE_TEMP)
            self.unavailable_after_sec = SENSOR_UNAVAILABLE_SEC
            self._info_value_cfg = {INFO_VALUE_BATTERY : {ATTR_POS : 3111},
                                    INFO_VALUE_TILT : {ATTR_POS : 3109},
                                    INFO_VALUE_VIBRATION :
                                        {ATTR_POS : 6110,
                                         ATTR_AUTO_SET: [0, 60]},
                                    INFO_VALUE_TEMP: {ATTR_POS : 3101},
                                    INFO_VALUE_ILLUMINANCE: {ATTR_POS : 3106}
            }
            self._add_device(DoorWindow(self, 3108))
        elif self.type == 'SHBDUO-1':
            self._add_device(Duo(self))
            self._add_device(PowerMeter(self, 0, [213], tot_pos=214))
        elif self.type == 'SHVIN-1':
            self._add_device(Vintage(self))
            self._add_device(PowerMeter(self, 0, [213], tot_pos=214))
        elif self.type == 'SHBTN-1':
            self._add_device(Switch(self, 0, 118, 119, 120, True))
        elif self.type == 'SHIX3-1':
            self._add_device(Switch(self, 1, 118, 119, 120))
            self._add_device(Switch(self, 2, 128, 129, 130))
            self._add_device(Switch(self, 3, 138, 139, 140))
        elif self.type == 'SHGS-1':
            self._info_value_cfg = {INFO_VALUE_PPM : {ATTR_POS : 122},
                                    INFO_VALUE_SENSOR : {ATTR_POS : 118}
            }
            self._add_device(Gas(self, 119))
        elif self.type == 'SHAIR-1':
            self._info_value_cfg = {
                INFO_VALUE_TEMP: {ATTR_POS : 119,
                                  ATTR_PATH : 'ext_temperatures/0'},
                INFO_VALUE_TOTAL_WORK_TIME: {ATTR_POS : 121,
                                             ATTR_PATH : 'total_work_time'}
            }
            self._add_device(Relay(self, 0, 112, 111, 118))
            self._add_device(PowerMeter(self, 0, [111]))
            self._add_device(Switch(self, 0, 118))

    def _add_device(self, dev, lazy_load=False):
        dev.lazy_load = lazy_load
        self.devices.append(dev)
        #self.parent.add_device(dev, self.discovery_src)
        return dev

    def _reload_devices(self):
        for device in self.devices:
            self.parent.remove_device(device, self.discovery_src)
            device.close()
        self.devices = []
        self._setup()

    def fw_version(self):
        return self.info_values.get(INFO_VALUE_FW_VERSION)

    def latest_fw_version(self, beta = False):
        if beta:
            return  self.parent._firmware_mgr.version(self.type, True)
        else:
            return self.info_values.get(INFO_VALUE_LATEST_FIRMWARE_VERSION)

    def has_fw_update(self, beta = False):
        latest = self.latest_fw_version(beta)
        current = self.fw_version()
        return latest and current and latest != current

    def friendly_name(self):
        try:
            if self.parent.cloud:
                name = self.parent.cloud.get_device_name(self.id.lower())
                if name:
                    return name
        except:
            pass
        return self.type_name() + ' - ' + self.id

    def room_name(self):
        if self.parent.cloud:
            return self.parent.cloud.get_room_name(self.id.lower())

    def type_name(self):
        """Type friendly name"""
        try:
            name = SHELLY_TYPES[self.type]['name']
        except:
            name = self.type
        return name

    def available(self):
        """Return if device available"""
        if self.unavailable_after_sec is None:
            return True
        if self.last_updated is None:
            return False
        diff = datetime.now() - self.last_updated
        return diff.total_seconds() <= self.unavailable_after_sec
