import concurrent.futures
import getpass
import logging
import platform
from typing import Optional

logger = logging.getLogger(__name__)


if platform.system() == "Windows":
    import ldap3
    import ms_active_directory

    def validate_aind_username(
        username: str,
        domain: str = "corp.alleninstitute.org",
        domain_username: Optional[str] = None,
        timeout: Optional[float] = 2,
    ) -> bool:
        """
        Validates if the given username is in the AIND active directory.
        See https://github.com/AllenNeuralDynamics/aind-watchdog-service/issues/110#issuecomment-2828869619

        Args:
            username (str): The username to validate.

        Returns:
            bool: True if the username is valid, False otherwise.
        """

        def _helper(username: str, domain: str, domain_username: Optional[str]) -> bool:
            if domain_username is None:
                domain_username = getpass.getuser()

            _domain = ms_active_directory.ADDomain(domain)
            session = _domain.create_session_as_user(
                domain_username,
                authentication_mechanism=ldap3.SASL,
                sasl_mechanism=ldap3.GSSAPI,
            )
            return session.find_user_by_name(username) is not None

        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_helper, username, domain, domain_username)
                result = future.result(timeout=timeout)
                return result
        except concurrent.futures.TimeoutError as e:
            logger.error("Timeout occurred while validating username: %s", e)
            e.add_note("Timeout occurred while validating username")
            raise e

else:

    def validate_aind_username(
        username: str,
        domain: str = "corp.alleninstitute.org",
        domain_username: Optional[str] = None,
        timeout: Optional[float] = 2,
    ) -> bool:
        """
        Validates if the given username is in the AIND active directory.
        This function is a no-op on non-Windows platforms.

        Args:
            username (str): The username to validate.

        Returns:
            bool: Always returns True on non-Windows platforms.
        """
        logger.warning("Active Directory validation is not implemented for non-Windows platforms")
        return True
