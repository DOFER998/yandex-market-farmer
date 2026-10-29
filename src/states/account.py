from aiogram.fsm.state import State, StatesGroup


class AccountForm(StatesGroup):
    name = State()
    delete = State()
