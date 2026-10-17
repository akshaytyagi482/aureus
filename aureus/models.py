"""Plain data records shared by the ingestion adapters and the engines."""

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class InvoiceLine:
    description: str
    hsn: str
    taxable_value: float
    quantity: float = 1.0

    @property
    def unit_value(self) -> float:
        return self.taxable_value / self.quantity if self.quantity else self.taxable_value


@dataclass
class Invoice:
    """A purchase invoice, either as booked in accounts or as uploaded by the supplier.

    Amounts are rupees. `portal_*` fields are only filled for GSTR-2B records.
    """

    supplier_gstin: str
    supplier_name: str
    invoice_no: str
    invoice_date: date
    taxable_value: float
    igst: float = 0.0
    cgst: float = 0.0
    sgst: float = 0.0
    cess: float = 0.0
    place_of_supply: str = ""
    reverse_charge: bool = False
    lines: list[InvoiceLine] = field(default_factory=list)
    source: str = ""
    return_period: str = ""
    itc_available: bool = True
    itc_unavailable_reason: str = ""
    supplier_filing_date: str = ""
    id: int | None = None

    @property
    def total_tax(self) -> float:
        return round(self.igst + self.cgst + self.sgst + self.cess, 2)

    @property
    def invoice_value(self) -> float:
        return round(self.taxable_value + self.total_tax, 2)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["invoice_date"] = self.invoice_date.isoformat()
        data["total_tax"] = self.total_tax
        data["invoice_value"] = self.invoice_value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Invoice":
        data = {k: v for k, v in data.items() if k not in ("total_tax", "invoice_value")}
        data["invoice_date"] = date.fromisoformat(data["invoice_date"])
        data["lines"] = [InvoiceLine(**line) for line in data.get("lines", [])]
        return cls(**data)
