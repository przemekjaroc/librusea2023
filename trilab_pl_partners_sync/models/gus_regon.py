import logging
from enum import Enum
from typing import Union, Iterable, Dict, Any, Optional
from zeep import Client as ZClient
from zeep.exceptions import XMLParseError
from lxml import etree

__all__ = ['GusException', 'GusClient', 'ReportType', 'EntityType']

_logger = logging.getLogger(__name__)

ITER_STR = Iterable[str]
ITER_STR_OR_STR = Union[ITER_STR, str]


class GusException(Exception):
    errno: int
    strerror: str

    def __init__(self, strerror: str, errno: int = None, *args: object) -> None:
        self.strerror = strerror
        self.errno = errno
        super().__init__(strerror, *args)


class ReportType(Enum):
    OsFizycznaDaneOgolne = 'BIR11OsFizycznaDaneOgolne'
    OsFizycznaDzialalnoscCeidg = 'BIR11OsFizycznaDzialalnoscCeidg'
    OsFizycznaDzialalnoscRolnicza = 'BIR11OsFizycznaDzialalnoscRolnicza'
    OsFizycznaDzialalnoscPozostala = 'BIR11OsFizycznaDzialalnoscPozostala'
    OsFizycznaDzialalnoscSkreslona = 'BIR11OsFizycznaDzialalnoscSkreslonaDo20141108'
    OsFizycznaPkd = 'BIR11OsFizycznaPkd'
    OsFizycznaListaJednLokalnych = 'BIR11OsFizycznaListaJednLokalnych'
    JednLokalnaOsFizycznej = 'BIR11JednLokalnaOsFizycznej'
    JednLokalnaOsFizycznejPkd = 'JednLokalnaOsFizycznejPkd'
    OsPrawna = 'BIR11OsPrawna'
    JednLokalnaOsPrawnej = 'BIR11JednLokalnaOsPrawnej'
    JednLokalnaOsPrawnejPkd = 'BIR11JednLokalnaOsPrawnejPkd'
    PrawnaSpCywilnaWspolnicy = 'BIR11OsPrawnaSpCywilnaWspolnicy'
    TypPodmiotu = 'BIR11TypPodmiotu'


class EntityType(Enum):
    OsPrawna = 'P'
    OsFizyczna = 'F'
    JednostkaLokalnaOsPrawnej = 'LP'
    JednostkaLokalnaOsFizycznej = 'LF'


class GusClient:
    BIR_SETTINGS = {
        'TEST': {
            'WSDL': 'https://wyszukiwarkaregontest.stat.gov.pl/wsBIR/wsdl/UslugaBIRzewnPubl-ver11-test.wsdl',
            'API_KEY': 'abcde12345abcde12345',
        },
        'PROD': {
            'WSDL': 'https://wyszukiwarkaregon.stat.gov.pl/wsBIR/wsdl/UslugaBIRzewnPubl-ver11-prod.wsdl',
        },
    }

    RESPONSE_DATA = 'dane'
    RESPONSE_ERROR_MESSAGE = 'ErrorMessageEn'
    RESPONSE_ERROR_NUMBER = 'ErrorCode'

    def __init__(self, api_key: str = None, sandbox: bool = False) -> None:
        config = GusClient.BIR_SETTINGS[sandbox and 'TEST' or 'PROD']

        self.api_key = config.get('API_KEY', api_key)
        self._sid = None

        if not self.api_key:
            error_msg = 'api_key is required in production environment.'
            _logger.error(error_msg)
            raise GusException(error_msg)

        self.client = ZClient(config.get('WSDL'))

    def _call_api(self, method_name: str, parse_xml_response: bool = False, raise_exception: bool = True,
                  **request_data) -> Any:
        try:
            _logger.debug(f'Call API: method: {method_name}')
            if not self._sid:
                self.authenticate()
            response = getattr(self.client.service, method_name)(**request_data)

            if response and parse_xml_response:
                response = self._parse_xml_data(response)
                if GusClient.RESPONSE_DATA in response:
                    if raise_exception and GusClient.RESPONSE_ERROR_NUMBER in response[GusClient.RESPONSE_DATA]:
                        raise GusException(response[GusClient.RESPONSE_DATA][GusClient.RESPONSE_ERROR_MESSAGE],
                                           response[GusClient.RESPONSE_DATA][GusClient.RESPONSE_ERROR_NUMBER])
                    response = response[GusClient.RESPONSE_DATA]

        except XMLParseError as exception:
            _logger.exception(f'Error while calling API: {exception}')
            raise exception

        return response

    def _is_session_valid(self) -> bool:
        res = self._call_api('GetValue', pNazwaParametru='StatusSesji')
        return res == '1'

    @staticmethod
    def _parse_xml_data(xml_data: str) -> Dict:
        def dictify(r, root=True):
            if root:
                return dictify(r, False)

            children = r.getchildren()
            if children:
                result = {}
                for c in children:
                    if c.tag in result:
                        if not isinstance(result[c.tag], list):
                            result[c.tag] = [result[c.tag]]
                        result[c.tag].append(dictify(c, False))
                    else:
                        result[c.tag] = dictify(c, False)
                return result
            else:
                return r.text

        return dictify(etree.fromstring(xml_data))

    def authenticate(self, raise_exception: bool = True) -> bool:
        self._sid = self.client.service.Zaloguj(pKluczUzytkownika=self.api_key)

        self.client.transport.session.headers.update({'sid': self._sid})

        if self._is_session_valid():
            _logger.debug('Authenticated successfully.')
            return True

        error_msg = 'Authentication Failed'
        _logger.error(error_msg)

        if raise_exception:
            raise GusException(error_msg)
        else:
            return False

    def get_partners_data(self, krs: ITER_STR_OR_STR = None, nip: ITER_STR_OR_STR = None,
                          raise_exception: bool = True) -> Optional[Dict]:
        def _validate(field, value, lengths):
            if isinstance(value, str):
                value = [value]
            if any([v for v in value if len(v) not in lengths]):
                raise GusException(f'Invalid length for {field}')

        if krs:
            _validate('krs', krs, [10])
            search_data = krs
            search_key = 'Krs' if isinstance(krs, str) else 'Krsy'

        elif nip:
            _validate('nip', nip, [10])
            search_data = nip
            search_key = 'Nip' if isinstance(nip, str) else 'Nipy'

        else:
            error_msg = 'At least one parameter is required.'
            _logger.error(error_msg)
            raise AttributeError(error_msg)

        if not isinstance(search_data, str):
            search_data = tuple(search_data)
            if len(search_data) > 20:
                error_msg = 'Maximum number of identifiers is 20'
                _logger.error(error_msg)
                raise GusException(error_msg)
            search_data = ','.join(search_data)

        try:
            return self._call_api('DaneSzukajPodmioty',
                                  pParametryWyszukiwania={search_key: search_data},
                                  parse_xml_response=True,
                                  raise_exception=raise_exception)
        except GusException as e:
            if e.errno == 4:  # no results found
                return None
            raise e

    def get_full_report(self, regon: str, report: ReportType, raise_exception: bool = True) -> Dict:
        if len(regon) not in (9, 14):
            error_msg = 'Invalid REGON length'
            _logger.error(error_msg)
            raise GusException(error_msg)

        return self._call_api('DanePobierzPelnyRaport', pRegon=regon, pNazwaRaportu=report.value,
                              parse_xml_response=True, raise_exception=raise_exception)
