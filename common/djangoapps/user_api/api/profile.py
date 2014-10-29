"""Python API for user profiles.

Profile information includes a student's demographic information and preferences,
but does NOT include basic account information such as username, password, and
email address.

"""


from user_api.models import User, UserProfile, UserPreference
from user_api.helpers import intercept_errors


class ProfileRequestError(Exception):
    """ The request to the API was not valid. """
    pass


class ProfileUserNotFound(ProfileRequestError):
    """ The requested user does not exist. """
    pass


class ProfileInvalidField(ProfileRequestError):
    """ The proposed value for a field is not in a valid format. """

    def __init__(self, field, value):
        self.field = field
        self.value = value

    def __str__(self):
        return u"Invalid value '{value}' for profile field '{field}'".format(
            value=self.value,
            field=self.field
        )


class ProfileInternalError(Exception):
    """ An error occurred in an API call. """
    pass


FULL_NAME_MAX_LENGTH = 255


@intercept_errors(ProfileInternalError, ignore_errors=[ProfileRequestError])
def profile_info(username):
    """Retrieve a user's profile information.

    Searches either by username or email.

    At least one of the keyword args must be provided.

    Args:
        username (unicode): The username of the account to retrieve.

    Returns:
        dict: If profile information was found.
        None: If the provided username did not match any profiles.

    """
    try:
        profile = UserProfile.objects.get(user__username=username)
    except UserProfile.DoesNotExist:
        return None

    profile_dict = {
        u'username': profile.user.username,
        u'email': profile.user.email,
        u'full_name': profile.name,
    }

    return profile_dict


@intercept_errors(ProfileInternalError, ignore_errors=[ProfileRequestError])
def update_profile(username, full_name=None):
    """Update a user's profile.

    Args:
        username (unicode): The username associated with the account.

    Keyword Args:
        full_name (unicode): If provided, set the user's full name to this value.

    Returns:
        None

    Raises:
        ProfileRequestError: If there is no profile matching the provided username.

    """
    try:
        profile = UserProfile.objects.get(user__username=username)
    except UserProfile.DoesNotExist:
        raise ProfileUserNotFound

    if full_name is not None:
        name_length = len(full_name)
        if name_length > FULL_NAME_MAX_LENGTH or name_length == 0:
            raise ProfileInvalidField("full_name", full_name)
        else:
            profile.update_name(full_name)


@intercept_errors(ProfileInternalError, ignore_errors=[ProfileRequestError])
def preference_info(username):
    """Retrieve information about a user's preferences.

    Args:
        username (unicode): The username of the account to retrieve.

    Returns:
        dict: Empty if there is no user

    """
    preferences = UserPreference.objects.filter(user__username=username)

    preferences_dict = {}
    for preference in preferences:
        preferences_dict[preference.key] = preference.value

    return preferences_dict


@intercept_errors(ProfileInternalError, ignore_errors=[ProfileRequestError])
def update_preferences(username, **kwargs):
    """Update a user's preferences.

    Sets the provided preferences for the given user.

    Args:
        username (unicode): The username of the account to retrieve.

    Keyword Args:
        **kwargs (unicode): Arbitrary key-value preference pairs

    Returns:
        None

    Raises:
        ProfileUserNotFound

    """
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        raise ProfileUserNotFound
    else:
        for key, value in kwargs.iteritems():
            UserPreference.set_preference(user, key, value)
