import json
import urllib.request
import re
from threading import Lock

from .compat import s
from .utils import exception_log
from .const import (
    REGEX_VER
)

_URL_SHELLY_FIRMWARES = "https://repo.shelly.cloud/files/firmware"


class Singleton:
    """
    A non-thread-safe helper class to ease implementing singletons.
    This should be used as a decorator -- not a metaclass -- to the
    class that should be a singleton.

    The decorated class can define one `__init__` function that
    takes only the `self` argument. Also, the decorated class cannot be
    inherited from. Other than that, there are no restrictions that apply
    to the decorated class.

    To get the singleton instance, use the `instance` method. Trying
    to use `__call__` will result in a `TypeError` being raised.

    """

    def __init__(self, decorated):
        self._decorated = decorated

    def instance(self):
        """
        Returns the singleton instance. Upon its first call, it creates a
        new instance of the decorated class and calls its `__init__` method.
        On all subsequent calls, the already created instance is returned.

        """
        try:
            return self._instance
        except AttributeError:
            self._instance = self._decorated()
            return self._instance

    def __call__(self):
        raise TypeError('Singletons must be accessed through `instance()`.')

    def __instancecheck__(self, inst):
        return isinstance(inst, self._decorated)


@Singleton
class FirmwareManager:

    def __init__(self):
        self.last_updated = None
        self._firmwares = None
        self._downloaded = False
        self._lock = Lock()

    @property
    def _list_firmwares(self):
        if self._downloaded:
            return self._firmwares

        with self._lock:
            if self._firmwares is None:
                self._firmwares = self._http_get(_URL_SHELLY_FIRMWARES)
                self._downloaded = True
            return self._firmwares

    def _http_get(self, url):
        f = None
        try:
            f = urllib.request.urlopen(url)
            body = f.read()
            res = json.loads(s(body))
            return res['data']
        except Exception as ex:
            exception_log(ex, "Error http GET: http://{}", url)
        finally:
            if f:
                f.close()
        return {}

    def format(self, value):
        ver = re.search(REGEX_VER, value)
        if ver:
            return ver.group(2)  # + " (" + ver.group(1) + ")"
        return value

    def version(self, shelly_type, beta):
        if shelly_type in self._list_firmwares:
            cfg = self._list_firmwares[shelly_type]
            if beta and 'beta_ver' in cfg:
                return self.format(cfg['beta_ver'])
            else:
                return self.format(cfg['version'])

    def url(self, shelly_type, beta):
        if shelly_type in self._list_firmwares:
            cfg = self._list_firmwares[shelly_type]
            if beta and 'beta_ver' in cfg:
                return cfg['beta_url']
            else:
                return None
