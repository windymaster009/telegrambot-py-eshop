from aiogram.fsm.state import State, StatesGroup


class CustomerState(StatesGroup):
    choosing_quantity = State()
    awaiting_order_proof = State()
    entering_deposit_amount = State()
    awaiting_deposit_proof = State()
    entering_refund_amount = State()
    awaiting_refund_qr = State()


class AdminState(StatesGroup):
    adding_product = State()
    editing_product = State()
    adding_stock = State()
