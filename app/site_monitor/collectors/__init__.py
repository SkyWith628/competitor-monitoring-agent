from app.core.models import Brand
from app.site_monitor.collectors.eightseconds import EightSecondsCollector
from app.site_monitor.collectors.musinsa_standard import MusinsaStandardCollector
from app.site_monitor.collectors.spao import SpaoCollector
from app.site_monitor.collectors.uniqlo import UniqloCollector
from app.site_monitor.collectors.whoau import WhoauCollector

SITE_COLLECTORS = {
    Brand.SPAO: SpaoCollector,
    Brand.EIGHT_SECONDS: EightSecondsCollector,
    Brand.MUSINSA_STANDARD: MusinsaStandardCollector,
    Brand.UNIQLO: UniqloCollector,
    Brand.WHOAU: WhoauCollector,
}
