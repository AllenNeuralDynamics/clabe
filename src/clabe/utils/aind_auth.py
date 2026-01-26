import concurrent.futures
import logging
import platform
from typing import Optional

logger = logging.getLogger(__name__)

_ad_logger = logging.getLogger(
    "ms_active_directory"
)  # This library is annoyingly verbose at the INFO level. We will turn it off unless the root is at DEBUG level


if platform.system() == "Windows":

    def validate_aind_username(
        username: str,
        domain: str = "corp.alleninstitute.org",
        domain_username: Optional[str] = None,
        timeout: Optional[float] = 2,
    ) -> bool:
        """
        Validates if the given username exists in the AIND Active Directory.

        This function authenticates with the corporate Active Directory and searches
        for the specified username to verify its existence within the organization.
        See https://github.com/AllenNeuralDynamics/aind-watchdog-service/issues/110#issuecomment-2828869619

        Args:
            username: The username to validate against Active Directory
            domain: The Active Directory domain to search. Defaults to Allen Institute domain
            domain_username: Username for domain authentication. Defaults to current user
            timeout: Timeout in seconds for the validation operation

        Returns:
            bool: True if the username exists in Active Directory, False otherwise

        Raises:
            concurrent.futures.TimeoutError: If the validation operation times out

        Example:
            ```python
            # Validate a username in Active Directory
            is_valid = validate_aind_username("j.doe")
            ```
        """
        import getpass  # type: ignore[import]

        import ldap3  # type: ignore[import]
        import ms_active_directory  # type: ignore[import]

        def _helper(username: str, domain: str, domain_username: Optional[str]) -> bool:
            """A function submitted to a thread pool to validate the username."""
            if not logger.isEnabledFor(logging.DEBUG):
                _ad_logger.disabled = True
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
            raise

else:

    def validate_aind_username(
        username: str,
        domain: str = "corp.alleninstitute.org",
        domain_username: Optional[str] = None,
        timeout: Optional[float] = 2,
    ) -> bool:
        """
        Validates if the given username is in the AIND Active Directory.

        This function is a no-op on non-Windows platforms since Active Directory
        authentication is not available.

        This function always returns True on non-Windows platforms.

        Args:
            username: The username to validate (ignored on non-Windows platforms)
            domain: The Active Directory domain (ignored on non-Windows platforms)
            domain_username: Username for domain authentication (ignored on non-Windows platforms)
            timeout: Timeout for the validation operation (ignored on non-Windows platforms)

        Returns:
            bool: Always returns True on non-Windows platforms
        """
        logger.warning("Active Directory validation is not implemented for non-Windows platforms")
        return True
