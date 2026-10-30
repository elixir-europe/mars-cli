import keyring
import pytest
from keyring.backend import KeyringBackend

from mars_lib.credential import CredentialManager


class InMemoryKeyring(KeyringBackend):
    """Keyring backend that keeps passwords in memory, so tests don't touch the system keyring."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.passwords = {}

    def set_password(self, service, username, password):
        self.passwords[(service, username)] = password

    def get_password(self, service, username):
        return self.passwords.get((service, username))

    def delete_password(self, service, username):
        self.passwords.pop((service, username), None)


@pytest.fixture
def in_memory_keyring():
    original_keyring = keyring.get_keyring()
    keyring.set_keyring(InMemoryKeyring())
    yield
    keyring.set_keyring(original_keyring)


def test_create_credentials_manager():
    cm = CredentialManager("webin")
    assert cm is not None

    with pytest.raises(
        ValueError, match="Invalid authentication provider: invalid_provider"
    ):
        CredentialManager("invalid_provider")


def test_set_password_keyring(in_memory_keyring):
    cm = CredentialManager("webin")
    cm.set_password_keyring("username", "password")
    assert cm.get_password_keyring("username") == "password"
