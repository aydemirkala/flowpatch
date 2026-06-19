"""LDAP/AD integration service for user authentication."""
from __future__ import annotations

from typing import Optional, Tuple
from dataclasses import dataclass

from ..logging_utils import log_event


@dataclass
class LdapUser:
    """Represents a user from LDAP directory."""
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    groups: list[str] = None
    
    def __post_init__(self):
        if self.groups is None:
            self.groups = []


def _get_ldap_connection(
    server_url: str, 
    port: int, 
    use_ssl: bool, 
    bind_dn: str, 
    bind_password: str,
    ca_cert: Optional[str] = None,
    skip_cert_verify: bool = False
):
    """Create and return an LDAP connection."""
    try:
        import ldap3
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        log_event("ldap.error", error="ldap3 library not installed. Run: pip install ldap3")
        return None
    
    try:
        # Build server URL
        if use_ssl and not server_url.startswith("ldaps://"):
            full_url = f"ldaps://{server_url}"
        elif not use_ssl and not server_url.startswith("ldap://"):
            full_url = f"ldap://{server_url}"
        else:
            full_url = server_url
        
        # Configure TLS settings
        tls_config = None
        if use_ssl:
            if skip_cert_verify:
                # Skip certificate verification (not recommended for production)
                tls_config = Tls(validate=ssl.CERT_NONE)
            elif ca_cert:
                # Use custom CA certificate
                import tempfile
                import os
                # Write CA cert to temp file
                ca_file = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
                ca_file.write(ca_cert)
                ca_file.close()
                try:
                    tls_config = Tls(ca_certs_file=ca_file.name, validate=ssl.CERT_REQUIRED)
                finally:
                    # Clean up temp file after creating tls config
                    pass  # Will be cleaned up by OS or on next call
            else:
                # Use system CA certificates
                tls_config = Tls(validate=ssl.CERT_REQUIRED)
        
        server = Server(full_url, port=port, use_ssl=use_ssl, tls=tls_config, get_info=ALL)
        conn = Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
        return conn
    except Exception as e:
        log_event("ldap.connection.error", error=str(e))
        return None


def test_ldap_connection(
    server_url: str,
    port: int,
    use_ssl: bool,
    bind_dn: str,
    bind_password: str,
    ca_cert: Optional[str] = None,
    skip_cert_verify: bool = False
) -> Tuple[bool, str]:
    """Test LDAP connection with provided credentials.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        import ldap3
    except ImportError:
        return False, "ldap3 library not installed. Run: pip install ldap3"
    
    conn = _get_ldap_connection(server_url, port, use_ssl, bind_dn, bind_password, ca_cert, skip_cert_verify)
    if conn is None:
        return False, "Failed to connect to LDAP server. Check server URL, credentials, and certificate settings."
    
    try:
        conn.unbind()
        log_event("ldap.connection.test", success=True, server=server_url)
        return True, "Connection successful"
    except Exception as e:
        log_event("ldap.connection.test", success=False, error=str(e))
        return False, str(e)


def detect_base_dn(
    server_url: str,
    port: int,
    use_ssl: bool,
    bind_dn: str,
    bind_password: str,
    ca_cert: Optional[str] = None,
    skip_cert_verify: bool = False
) -> Tuple[bool, str]:
    """Auto-detect the base DN from the LDAP server.
    
    Returns:
        Tuple of (success: bool, base_dn_or_error: str)
    """
    try:
        import ldap3
        from ldap3 import Server, Connection, ALL
    except ImportError:
        return False, "ldap3 library not installed"
    
    conn = _get_ldap_connection(server_url, port, use_ssl, bind_dn, bind_password, ca_cert, skip_cert_verify)
    if conn is None:
        return False, "Failed to connect to LDAP server"
    
    try:
        # Try to get naming contexts from server info
        server_info = conn.server.info
        if server_info and hasattr(server_info, 'naming_contexts') and server_info.naming_contexts:
            base_dn = server_info.naming_contexts[0]
            conn.unbind()
            return True, base_dn
        
        # Fallback: try to extract from bind DN
        if bind_dn:
            parts = bind_dn.split(',')
            dc_parts = [p for p in parts if p.upper().startswith('DC=')]
            if dc_parts:
                base_dn = ','.join(dc_parts)
                conn.unbind()
                return True, base_dn
        
        conn.unbind()
        return False, "Could not detect base DN"
    except Exception as e:
        return False, str(e)


def test_base_dn(
    server_url: str,
    port: int,
    use_ssl: bool,
    bind_dn: str,
    bind_password: str,
    base_dn: str,
    ca_cert: Optional[str] = None,
    skip_cert_verify: bool = False
) -> Tuple[bool, str]:
    """Test if the base DN is valid by searching for any entry.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        import ldap3
        from ldap3 import SUBTREE
    except ImportError:
        return False, "ldap3 library not installed"
    
    conn = _get_ldap_connection(server_url, port, use_ssl, bind_dn, bind_password, ca_cert, skip_cert_verify)
    if conn is None:
        return False, "Failed to connect to LDAP server"
    
    try:
        conn.search(
            search_base=base_dn,
            search_filter='(objectClass=*)',
            search_scope=ldap3.BASE,
            attributes=['objectClass']
        )
        
        if conn.entries:
            conn.unbind()
            return True, f"Base DN is valid. Found: {base_dn}"
        else:
            conn.unbind()
            return False, "Base DN not found or no access"
    except Exception as e:
        return False, str(e)


def authenticate_user(
    server_url: str,
    port: int,
    use_ssl: bool,
    bind_dn: str,
    bind_password: str,
    base_dn: str,
    user_search_filter: str,
    user_search_base: Optional[str],
    username: str,
    password: str,
    username_attribute: str = "sAMAccountName",
    email_attribute: str = "mail",
    display_name_attribute: str = "displayName",
    ca_cert: Optional[str] = None,
    skip_cert_verify: bool = False
) -> Tuple[bool, Optional[LdapUser], str]:
    """Authenticate a user against LDAP.
    
    Args:
        server_url: LDAP server URL
        port: LDAP port
        use_ssl: Whether to use SSL
        bind_dn: Service account DN for initial bind
        bind_password: Service account password
        base_dn: Base DN for searches
        user_search_filter: Filter to find user (use {username} placeholder)
        user_search_base: Optional specific search base for users
        username: Username to authenticate
        password: User's password
        username_attribute: LDAP attribute for username
        email_attribute: LDAP attribute for email
        display_name_attribute: LDAP attribute for display name
    
    Returns:
        Tuple of (success: bool, user: LdapUser or None, message: str)
    """
    try:
        import ldap3
        from ldap3 import Server, Connection, SUBTREE
    except ImportError:
        return False, None, "ldap3 library not installed"
    
    # First, bind with service account to find the user
    conn = _get_ldap_connection(server_url, port, use_ssl, bind_dn, bind_password, ca_cert, skip_cert_verify)
    if conn is None:
        return False, None, "Failed to connect to LDAP server"
    
    try:
        # Build search filter
        search_filter = user_search_filter.replace("{username}", username)
        search_base = user_search_base or base_dn
        
        # Search for user
        conn.search(
            search_base=search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=[username_attribute, email_attribute, display_name_attribute, 'memberOf']
        )
        
        if not conn.entries:
            conn.unbind()
            log_event("ldap.auth.user_not_found", username=username)
            return False, None, "User not found in directory"
        
        user_entry = conn.entries[0]
        user_dn = user_entry.entry_dn
        
        # Extract user info
        user_email = str(user_entry[email_attribute].value) if email_attribute in user_entry and user_entry[email_attribute].value else None
        user_display_name = str(user_entry[display_name_attribute].value) if display_name_attribute in user_entry and user_entry[display_name_attribute].value else None
        user_groups = []
        if 'memberOf' in user_entry and user_entry['memberOf'].values:
            user_groups = [str(g) for g in user_entry['memberOf'].values]
        
        conn.unbind()
        
        # Now try to bind as the user to verify password
        try:
            import ssl
            from ldap3 import Tls
            
            if use_ssl and not server_url.startswith("ldaps://"):
                full_url = f"ldaps://{server_url}"
            elif not use_ssl and not server_url.startswith("ldap://"):
                full_url = f"ldap://{server_url}"
            else:
                full_url = server_url
            
            # Configure TLS for user bind
            tls_config = None
            if use_ssl:
                if skip_cert_verify:
                    tls_config = Tls(validate=ssl.CERT_NONE)
                elif ca_cert:
                    import tempfile
                    ca_file = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
                    ca_file.write(ca_cert)
                    ca_file.close()
                    tls_config = Tls(ca_certs_file=ca_file.name, validate=ssl.CERT_REQUIRED)
                else:
                    tls_config = Tls(validate=ssl.CERT_REQUIRED)
            
            server = Server(full_url, port=port, use_ssl=use_ssl, tls=tls_config)
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
            
            ldap_user = LdapUser(
                username=username,
                email=user_email,
                display_name=user_display_name,
                groups=user_groups
            )
            log_event("ldap.auth.success", username=username)
            return True, ldap_user, "Authentication successful"
            
        except Exception as e:
            log_event("ldap.auth.failed", username=username, error=str(e))
            return False, None, "Invalid password"
            
    except Exception as e:
        log_event("ldap.auth.error", username=username, error=str(e))
        return False, None, str(e)


def get_user_groups(
    server_url: str,
    port: int,
    use_ssl: bool,
    bind_dn: str,
    bind_password: str,
    user_dn: str
) -> list[str]:
    """Get all groups a user belongs to.
    
    Returns:
        List of group DNs
    """
    try:
        import ldap3
        from ldap3 import SUBTREE
    except ImportError:
        return []
    
    conn = _get_ldap_connection(server_url, port, use_ssl, bind_dn, bind_password)
    if conn is None:
        return []
    
    try:
        conn.search(
            search_base=user_dn,
            search_filter='(objectClass=*)',
            search_scope=ldap3.BASE,
            attributes=['memberOf']
        )
        
        groups = []
        if conn.entries and 'memberOf' in conn.entries[0]:
            groups = [str(g) for g in conn.entries[0]['memberOf'].values]
        
        conn.unbind()
        return groups
    except Exception:
        return []


def determine_role_from_groups(
    user_groups: list[str],
    admin_group_dn: Optional[str],
    analyst_group_dn: Optional[str],
    readonly_group_dn: Optional[str]
) -> str:
    """Determine user role based on group membership.
    
    Priority: admin > analyst > read-only
    
    Returns:
        Role name: 'admin', 'analyst', or 'read-only'
    """
    # Normalize group DNs for comparison (case-insensitive)
    user_groups_lower = [g.lower() for g in user_groups]
    
    if admin_group_dn and admin_group_dn.lower() in user_groups_lower:
        return 'admin'
    
    if analyst_group_dn and analyst_group_dn.lower() in user_groups_lower:
        return 'analyst'
    
    if readonly_group_dn and readonly_group_dn.lower() in user_groups_lower:
        return 'read-only'
    
    # Default to read-only if no group match but LDAP auth succeeded
    return 'read-only'

